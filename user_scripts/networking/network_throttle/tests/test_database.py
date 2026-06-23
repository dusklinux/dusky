import asyncio
import pytest
from datetime import datetime, date as datetime_date
import os

from src.database import (
    init_db, save_traffic_stats, save_dns_logs, get_rules, add_rule, delete_rule_by_value,
    get_quotas, set_quota, get_historical_report, get_top_talkers, cleanup_old_logs,
    get_quota_usage, update_quota_usage, reset_quota_status
)

@pytest.mark.asyncio
async def test_database_lifecycle():
    # 1. Initialize DB
    await init_db()
    
    # 2. Add and retrieve rules
    await add_rule("process", "test_app", "limit", "1mbit", "1mbit")
    await add_rule("process", "blocked_app", "block")
    
    rules = await get_rules()
    assert len(rules) >= 2
    
    rule_values = [r["target_value"] for r in rules]
    assert "test_app" in rule_values
    assert "blocked_app" in rule_values
    
    # 3. Add and retrieve quotas
    await set_quota("daily", 1024 * 1024 * 100, 0.8) # 100MB daily
    quotas = await get_quotas()
    assert len(quotas) >= 1
    assert quotas[0]["period"] == "daily"
    assert quotas[0]["limit_bytes"] == 1024 * 1024 * 100
    
    # 4. Save traffic statistics
    stats_list = [
        {
            "pid": 1234,
            "comm": "test_app",
            "exe": "/usr/bin/test_app",
            "uid": 1000,
            "username": "dusk",
            "cgroup_path": "/sys/fs/cgroup/netctl",
            "bytes_sent": 50000,
            "bytes_recv": 150000,
            "packets_sent": 50,
            "packets_recv": 150,
            "protocol": "TCP"
        }
    ]
    await save_traffic_stats(stats_list)
    
    # Verify top talkers
    talkers = await get_top_talkers(limit=10)
    assert len(talkers) >= 1
    assert talkers[0]["comm"] == "test_app"
    assert talkers[0]["total_bytes"] == 200000
    
    # Verify historical report
    report = await get_historical_report("day")
    assert report["total_sent"] >= 50000
    assert report["total_recv"] >= 150000
    
    # 5. Save DNS logs
    dns_list = [
        {
            "pid": 1234,
            "comm": "test_app",
            "query": "google.com",
            "qtype": "A"
        }
    ]
    await save_dns_logs(dns_list)
    
    # 6. Test quota usage state updates
    p_start = datetime_date.today()
    await update_quota_usage("daily", p_start, 50000, 150000, warned_80=True)
    usage = await get_quota_usage("daily", p_start)
    assert usage is not None
    assert usage["bytes_sent"] == 50000
    assert usage["bytes_recv"] == 150000
    assert usage["warned_80"] is True
    
    await reset_quota_status()
    usage_reset = await get_quota_usage("daily", p_start)
    assert usage_reset["warned_80"] is False
    
    # 7. Clean rules
    deleted = await delete_rule_by_value("test_app")
    assert deleted is True
    
    # 8. Test logs retention cleanup
    cleaned = await cleanup_old_logs(days=0) # Cleanup logs older than 0 days (everything)
    assert cleaned >= 1
