from datetime import date, timedelta
from typing import Dict, Any, List, Tuple, Optional

from src.database import get_quotas, get_quota_usage, update_quota_usage, reset_quota_status
from src.utils import logger, send_notification, format_bytes

class QuotaManager:
    """Checks and manages system-wide bandwidth consumption quotas (Daily, Weekly, Monthly)."""
    
    def __init__(self, traffic_shaper: Any):
        self.shaper = traffic_shaper
        self.is_throttled = False

    def get_period_start(self, period: str) -> date:
        """Calculate start date for daily, weekly, or monthly quota periods."""
        today = date.today()
        if period == "daily":
            return today
        elif period == "weekly":
            return today - timedelta(days=today.weekday())  # Start of week (Monday)
        elif period == "monthly":
            return date(today.year, today.month, 1)  # Start of month
        else:
            raise ValueError(f"Unknown quota period: {period}")

    async def update_and_check_quotas(self, bytes_sent: int, bytes_recv: int) -> bool:
        """
        Record current usage cycle bytes, check against limits,
        send warnings, and trigger global speed limits if quotas are violated.
        Returns True if a quota is actively exceeded.
        """
        quotas = await get_quotas()
        if not quotas:
            # If no quotas, make sure we are not throttled due to quota
            if self.is_throttled:
                logger.info("Quotas cleared. Restoring global limits.")
                self.shaper.set_global_limits(None, None)
                self.is_throttled = False
            return False
            
        total_delta = bytes_sent + bytes_recv
        if total_delta == 0 and not self.is_throttled:
            # Short circuit if no network activity and not currently throttled
            return False

        any_exceeded = False
        
        for q in quotas:
            period = q["period"]
            limit = q["limit_bytes"]
            warn_threshold = q["warning_threshold"]
            
            p_start = self.get_period_start(period)
            
            # Retrieve or create usage in DB
            usage = await get_quota_usage(period, p_start)
            if not usage:
                await update_quota_usage(period, p_start, 0, 0)
                usage = {
                    "bytes_sent": 0,
                    "bytes_recv": 0,
                    "warned_80": False,
                    "warned_90": False,
                    "blocked": False
                }
                
            # Add delta for this tick
            await update_quota_usage(period, p_start, bytes_sent, bytes_recv)
            
            curr_sent = usage["bytes_sent"] + bytes_sent
            curr_recv = usage["bytes_recv"] + bytes_recv
            curr_total = curr_sent + curr_recv
            
            # Check thresholds
            pct = curr_total / limit if limit > 0 else 0
            
            # 80% Warning
            if pct >= 0.8 and pct < 0.9 and not usage["warned_80"]:
                msg = f"You have consumed {format_bytes(curr_total)} ({pct*100:.1f}%) of your {period} quota ({format_bytes(limit)})."
                logger.warning(f"Quota Warning: {msg}")
                send_notification("Network Quota Warning", msg)
                await update_quota_usage(period, p_start, 0, 0, warned_80=True)
                
            # 90% Warning
            if pct >= 0.9 and pct < 1.0 and not usage["warned_90"]:
                msg = f"Urgent: You have consumed {format_bytes(curr_total)} ({pct*100:.1f}%) of your {period} quota ({format_bytes(limit)})."
                logger.warning(f"Quota Warning: {msg}")
                send_notification("Network Quota Warning", msg)
                await update_quota_usage(period, p_start, 0, 0, warned_90=True)
                
            # 100% Exceeded
            if pct >= 1.0:
                any_exceeded = True
                if not usage["blocked"]:
                    msg = f"Network quota exceeded! Consumed {format_bytes(curr_total)} of your {period} limit ({format_bytes(limit)}). Speed is throttled to 64kbit."
                    logger.error(f"Quota Exceeded: {msg}")
                    send_notification("Quota Exceeded - Network Restricted", msg)
                    await update_quota_usage(period, p_start, 0, 0, blocked=True)

        if any_exceeded:
            if not self.is_throttled:
                logger.error("Applying quota restriction: Throttling network interface to 64kbps.")
                self.shaper.set_global_limits("64kbit", "64kbit")
                self.is_throttled = True
            return True
        else:
            if self.is_throttled:
                logger.info("Quotas reset or within limits. Restoring global limits.")
                # We reset quota throttle but we don't know the exact user global limit here.
                # The caller daemon will restore user-defined global limits.
                self.is_throttled = False
            return False
            
    async def reset_quotas(self) -> None:
        """Reset quota statuses in the database."""
        await reset_quota_status()
        if self.is_throttled:
            self.shaper.set_global_limits(None, None)
            self.is_throttled = False
        logger.info("Quotas reset successfully.")
