import asyncio
from datetime import datetime, timedelta, date as datetime_date, UTC
from typing import List, Dict, Any, Optional
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Date, Boolean, Index, select, delete, func, desc
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

from src.utils import DB_PATH, logger

Base = declarative_base()

class TrafficStat(Base):
    __tablename__ = "traffic_stats"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True)
    pid: Mapped[int] = mapped_column(Integer, index=True)
    comm: Mapped[str] = mapped_column(String, index=True)
    exe: Mapped[str] = mapped_column(String, index=True)
    uid: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String)
    cgroup_path: Mapped[str] = mapped_column(String)
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    bytes_recv: Mapped[int] = mapped_column(Integer, default=0)
    packets_sent: Mapped[int] = mapped_column(Integer, default=0)
    packets_recv: Mapped[int] = mapped_column(Integer, default=0)
    protocol: Mapped[str] = mapped_column(String)  # "TCP" or "UDP"

class DnsLog(Base):
    __tablename__ = "dns_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True)
    pid: Mapped[int] = mapped_column(Integer, index=True)
    comm: Mapped[str] = mapped_column(String)
    query: Mapped[str] = mapped_column(String, index=True)
    qtype: Mapped[str] = mapped_column(String)

class Rule(Base):
    __tablename__ = "rules"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String)  # "process", "executable", "user", "cgroup"
    target_value: Mapped[str] = mapped_column(String, unique=True) # e.g. "steam", "/usr/bin/firefox", "dusk", "netctl_block"
    policy: Mapped[str] = mapped_column(String)       # "block" or "limit"
    limit_down: Mapped[Optional[str]] = mapped_column(String, nullable=True) # e.g. "5mbit"
    limit_up: Mapped[Optional[str]] = mapped_column(String, nullable=True)

class Quota(Base):
    __tablename__ = "quotas"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String, unique=True)  # "daily", "weekly", "monthly"
    limit_bytes: Mapped[int] = mapped_column(Integer)
    warning_threshold: Mapped[float] = mapped_column(Float, default=0.8) # 80%
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

class QuotaUsage(Base):
    __tablename__ = "quota_usages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String, index=True)  # "daily", "weekly", "monthly"
    period_start: Mapped[datetime_date] = mapped_column(Date, index=True)
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    bytes_recv: Mapped[int] = mapped_column(Integer, default=0)
    warned_80: Mapped[bool] = mapped_column(Boolean, default=False)
    warned_90: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)

# Async engine setup
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    """Initialize database and create tables if they do not exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully.")

async def save_traffic_stats(stats: List[Dict[str, Any]]) -> None:
    """Batch save traffic statistics to database."""
    if not stats:
        return
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for item in stats:
                stat = TrafficStat(
                    timestamp=item.get("timestamp", datetime.now(UTC).replace(tzinfo=None)),
                    pid=item["pid"],
                    comm=item["comm"],
                    exe=item["exe"],
                    uid=item["uid"],
                    username=item["username"],
                    cgroup_path=item["cgroup_path"],
                    bytes_sent=item["bytes_sent"],
                    bytes_recv=item["bytes_recv"],
                    packets_sent=item["packets_sent"],
                    packets_recv=item["packets_recv"],
                    protocol=item["protocol"]
                )
                session.add(stat)

async def save_dns_logs(logs: List[Dict[str, Any]]) -> None:
    """Batch save DNS query logs to database."""
    if not logs:
        return
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for item in logs:
                log = DnsLog(
                    timestamp=item.get("timestamp", datetime.now(UTC).replace(tzinfo=None)),
                    pid=item["pid"],
                    comm=item["comm"],
                    query=item["query"],
                    qtype=item["qtype"]
                )
                session.add(log)

async def get_rules() -> List[Dict[str, Any]]:
    """Retrieve all firewall and bandwidth limit rules."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule))
        rules = result.scalars().all()
        return [
            {
                "id": r.id,
                "target_type": r.target_type,
                "target_value": r.target_value,
                "policy": r.policy,
                "limit_down": r.limit_down,
                "limit_up": r.limit_up
            } for r in rules
        ]

async def add_rule(target_type: str, target_value: str, policy: str, 
                   limit_down: Optional[str] = None, limit_up: Optional[str] = None) -> None:
    """Add or update a network control rule."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check if rule exists
            stmt = select(Rule).where(Rule.target_value == target_value)
            result = await session.execute(stmt)
            rule = result.scalar_one_or_none()
            if rule:
                rule.target_type = target_type
                rule.policy = policy
                rule.limit_down = limit_down
                rule.limit_up = limit_up
            else:
                new_rule = Rule(
                    target_type=target_type,
                    target_value=target_value,
                    policy=policy,
                    limit_down=limit_down,
                    limit_up=limit_up
                )
                session.add(new_rule)

async def delete_rule(rule_id: int) -> bool:
    """Delete a rule by ID."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = delete(Rule).where(Rule.id == rule_id)
            result = await session.execute(stmt)
            return result.rowcount > 0

async def delete_rule_by_value(target_value: str) -> bool:
    """Delete a rule by its target value (e.g. process name)."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = delete(Rule).where(Rule.target_value == target_value)
            result = await session.execute(stmt)
            return result.rowcount > 0

async def get_quotas() -> List[Dict[str, Any]]:
    """Retrieve all quota definitions."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Quota))
        quotas = result.scalars().all()
        return [
            {
                "id": q.id,
                "period": q.period,
                "limit_bytes": q.limit_bytes,
                "warning_threshold": q.warning_threshold
            } for q in quotas
        ]

async def set_quota(period: str, limit_bytes: int, warning_threshold: float = 0.8) -> None:
    """Set or update quota limit for a given period."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(Quota).where(Quota.period == period)
            result = await session.execute(stmt)
            quota = result.scalar_one_or_none()
            if quota:
                quota.limit_bytes = limit_bytes
                quota.warning_threshold = warning_threshold
            else:
                new_quota = Quota(
                    period=period,
                    limit_bytes=limit_bytes,
                    warning_threshold=warning_threshold
                )
                session.add(new_quota)

async def get_quota_usage(period: str, period_start: datetime_date) -> Optional[Dict[str, Any]]:
    """Retrieve current usage details for a specific quota period."""
    async with AsyncSessionLocal() as session:
        stmt = select(QuotaUsage).where(
            (QuotaUsage.period == period) & (QuotaUsage.period_start == period_start)
        )
        result = await session.execute(stmt)
        usage = result.scalar_one_or_none()
        if usage:
            return {
                "id": usage.id,
                "period": usage.period,
                "period_start": usage.period_start,
                "bytes_sent": usage.bytes_sent,
                "bytes_recv": usage.bytes_recv,
                "warned_80": usage.warned_80,
                "warned_90": usage.warned_90,
                "blocked": usage.blocked
            }
        return None

async def update_quota_usage(period: str, period_start: datetime_date, 
                             bytes_sent: int, bytes_recv: int, 
                             warned_80: Optional[bool] = None, 
                             warned_90: Optional[bool] = None, 
                             blocked: Optional[bool] = None) -> None:
    """Update or insert current quota usage statistics."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(QuotaUsage).where(
                (QuotaUsage.period == period) & (QuotaUsage.period_start == period_start)
            )
            result = await session.execute(stmt)
            usage = result.scalar_one_or_none()
            if usage:
                usage.bytes_sent += bytes_sent
                usage.bytes_recv += bytes_recv
                if warned_80 is not None:
                    usage.warned_80 = warned_80
                if warned_90 is not None:
                    usage.warned_90 = warned_90
                if blocked is not None:
                    usage.blocked = blocked
            else:
                new_usage = QuotaUsage(
                    period=period,
                    period_start=period_start,
                    bytes_sent=bytes_sent,
                    bytes_recv=bytes_recv,
                    warned_80=warned_80 or False,
                    warned_90=warned_90 or False,
                    blocked=blocked or False
                )
                session.add(new_usage)

async def reset_quota_status() -> None:
    """Reset the blocked/warned statuses for all active quotas."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(QuotaUsage).where((QuotaUsage.blocked == True) | (QuotaUsage.warned_80 == True) | (QuotaUsage.warned_90 == True))
            result = await session.execute(stmt)
            usages = result.scalars().all()
            for usage in usages:
                usage.blocked = False
                usage.warned_80 = False
                usage.warned_90 = False

async def get_historical_report(period_type: str) -> Dict[str, Any]:
    """
    Generate reports aggregating total usage.
    Supported periods: 'day' (hourly aggregates for last 24h), 'week' (daily aggregates for last 7 days),
    'month' (daily aggregates for last 30 days), 'year' (monthly aggregates for last 365 days).
    """
    async with AsyncSessionLocal() as session:
        now = datetime.now(UTC).replace(tzinfo=None)
        if period_type == "day":
            since = now - timedelta(days=1)
            # Group by hour (SQLite strftime format '%Y-%m-%d %H:00:00')
            group_fmt = "%Y-%m-%d %H:00:00"
        elif period_type == "week":
            since = now - timedelta(days=7)
            group_fmt = "%Y-%m-%d"
        elif period_type == "month":
            since = now - timedelta(days=30)
            group_fmt = "%Y-%m-%d"
        elif period_type == "year":
            since = now - timedelta(days=365)
            group_fmt = "%Y-%m"
        else:
            raise ValueError(f"Unknown report period type: {period_type}")

        # Query
        stmt = (
            select(
                func.strftime(group_fmt, TrafficStat.timestamp).label("label"),
                func.sum(TrafficStat.bytes_sent).label("sent"),
                func.sum(TrafficStat.bytes_recv).label("recv")
            )
            .where(TrafficStat.timestamp >= since)
            .group_by("label")
            .order_by("label")
        )
        
        result = await session.execute(stmt)
        rows = result.all()
        
        labels = []
        sent_data = []
        recv_data = []
        for r in rows:
            labels.append(r.label)
            sent_data.append(r.sent or 0)
            recv_data.append(r.recv or 0)
            
        return {
            "labels": labels,
            "bytes_sent": sent_data,
            "bytes_recv": recv_data,
            "total_sent": sum(sent_data),
            "total_recv": sum(recv_data)
        }

async def get_top_talkers(limit: int = 10) -> List[Dict[str, Any]]:
    """Return top applications by total accumulated bytes."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(
                TrafficStat.comm,
                TrafficStat.exe,
                func.sum(TrafficStat.bytes_sent).label("sent"),
                func.sum(TrafficStat.bytes_recv).label("recv")
            )
            .group_by(TrafficStat.comm, TrafficStat.exe)
            .order_by(desc(func.sum(TrafficStat.bytes_sent) + func.sum(TrafficStat.bytes_recv)))
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [
            {
                "comm": r.comm,
                "exe": r.exe,
                "bytes_sent": r.sent or 0,
                "bytes_recv": r.recv or 0,
                "total_bytes": (r.sent or 0) + (r.recv or 0)
            } for r in rows
        ]

async def cleanup_old_logs(days: int = 30) -> int:
    """Data retention policy: remove stats logs older than X days."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
            stmt1 = delete(TrafficStat).where(TrafficStat.timestamp < cutoff)
            stmt2 = delete(DnsLog).where(DnsLog.timestamp < cutoff)
            
            res1 = await session.execute(stmt1)
            res2 = await session.execute(stmt2)
            
            deleted_count = res1.rowcount + res2.rowcount
            if deleted_count > 0:
                logger.info(f"Database retention cleanup: deleted {deleted_count} logs older than {days} days.")
            return deleted_count
