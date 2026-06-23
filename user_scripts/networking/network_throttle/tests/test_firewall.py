import os
import subprocess
import pytest
from src.firewall import FirewallManager

@pytest.fixture(scope="module", autouse=True)
def skip_if_not_root():
    if os.geteuid() != 0:
        pytest.skip("Skipping firewall tests: root privileges required.")

def test_firewall_manager():
    fw = FirewallManager(cgroup_root="/sys/fs/cgroup/netctl_test")
    
    # 1. Initialize firewall table and output chain
    fw.init_firewall()
    
    # Check if table exists in nftables
    res = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True)
    assert fw.table_name in res.stdout
    
    # 2. Add block rule
    fw.add_block_rule("test_blocked")
    assert "test_blocked" in fw.blocked_apps
    assert os.path.exists(os.path.join(fw.cgroup_root, "block_test_blocked"))
    
    # Check nftables rule output
    res_list = subprocess.run(["nft", "list", "table", "inet", fw.table_name], capture_output=True, text=True)
    assert "netctl_test/block_test_blocked" in res_list.stdout
    
    # 3. Add allow rule & toggle default deny
    fw.add_allow_rule("test_allowed")
    assert "test_allowed" in fw.allowed_apps
    assert os.path.exists(os.path.join(fw.cgroup_root, "allow_test_allowed"))
    
    fw.set_default_policy(default_deny=True)
    assert fw.default_deny is True
    
    res_list_deny = subprocess.run(["nft", "list", "table", "inet", fw.table_name], capture_output=True, text=True)
    assert "netctl_test/allow_test_allowed" in res_list_deny.stdout
    assert "drop" in res_list_deny.stdout  # Default drop policy rule should be present
    
    # 4. Register dynamic throttle class marking
    fw.register_throttle_class("app_limit", 15)
    assert fw.throttled_classes["app_limit"] == 15
    assert os.path.exists(os.path.join(fw.cgroup_root, "throttle_15"))
    
    res_list_throttle = subprocess.run(["nft", "list", "table", "inet", fw.table_name], capture_output=True, text=True)
    assert "netctl_test/throttle_15" in res_list_throttle.stdout
    assert "mark set 0x0000000f" in res_list_throttle.stdout or "mark set 0x0f" in res_list_throttle.stdout or "mark set 15" in res_list_throttle.stdout
    
    # 5. Restore defaults and clean up
    fw.remove_block_rule("test_blocked")
    fw.remove_allow_rule("test_allowed")
    fw.unregister_throttle_class("app_limit")
    fw.set_default_policy(default_deny=False)
    
    fw.cleanup()
    
    # Verify table is deleted
    res_final = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True)
    assert fw.table_name not in res_final.stdout
    assert not os.path.exists(fw.cgroup_root)
