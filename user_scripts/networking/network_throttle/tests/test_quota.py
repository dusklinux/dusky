import os
import pytest
from datetime import date, timedelta
from typing import Any

from src.quota_manager import QuotaManager
from src.database import init_db, set_quota, get_quota_usage, update_quota_usage, reset_quota_status

class MockShaper:
    def __init__(self):
        self.limits = (None, None)
        
    def set_global_limits(self, limit_down: Any, limit_up: Any) -> None:
        self.limits = (limit_down, limit_up)

@pytest.mark.asyncio
async def test_quota_manager():
    await init_db()
    
    shaper = MockShaper()
    qm = QuotaManager(shaper)
    
    # 1. Test start date calculations
    today = date.today()
    assert qm.get_period_start("daily") == today
    assert qm.get_period_start("weekly") == today - timedelta(days=today.weekday())
    assert qm.get_period_start("monthly") == date(today.year, today.month, 1)
    
    # 2. Setup quotas
    limit_10mb = 10 * 1024 * 1024
    await set_quota("daily", limit_10mb, 0.8)
    
    # Reset usage to start fresh
    p_start = today
    await reset_quota_status()
    # Setup initial zero usage in DB
    await update_quota_usage("daily", p_start, 0, 0, warned_80=False, warned_90=False, blocked=False)
    
    # 3. Test 80% quota warning
    # Trigger 8.1MB usage (81%)
    bytes_sent = int(limit_10mb * 0.81 / 2)
    bytes_recv = bytes_sent
    
    exceeded = await qm.update_and_check_quotas(bytes_sent, bytes_recv)
    assert exceeded is False # not exceeded limit (100%) yet
    assert qm.is_throttled is False
    
    usage_80 = await get_quota_usage("daily", p_start)
    assert usage_80["warned_80"] is True
    assert usage_80["warned_90"] is False
    
    # 4. Test 90% quota warning
    # Trigger 9.1MB total usage (91%)
    bytes_sent_90 = int(limit_10mb * 0.11 / 2)
    bytes_recv_90 = bytes_sent_90
    
    exceeded = await qm.update_and_check_quotas(bytes_sent_90, bytes_recv_90)
    assert exceeded is False
    
    usage_90 = await get_quota_usage("daily", p_start)
    assert usage_90["warned_90"] is True
    assert usage_90["blocked"] is False
    
    # 5. Test 100% exceeded and global speed throttling
    # Trigger 10.5MB total usage (105%)
    bytes_sent_100 = int(limit_10mb * 0.15 / 2)
    bytes_recv_100 = bytes_sent_100
    
    exceeded = await qm.update_and_check_quotas(bytes_sent_100, bytes_recv_100)
    assert exceeded is True
    assert qm.is_throttled is True
    assert shaper.limits == ("64kbit", "64kbit") # global limits restricted to 64kbit!
    
    usage_100 = await get_quota_usage("daily", p_start)
    assert usage_100["blocked"] is True
    
    # 6. Reset quotas
    await qm.reset_quotas()
    assert qm.is_throttled is False
    assert shaper.limits == (None, None)
    
    usage_reset = await get_quota_usage("daily", p_start)
    assert usage_reset["blocked"] is False
    assert usage_reset["warned_80"] is False
    assert usage_reset["warned_90"] is False
