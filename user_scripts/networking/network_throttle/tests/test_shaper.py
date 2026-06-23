import subprocess
import os
import pytest
from src.traffic_shaper import TrafficShaper

TEST_IFACE = "netctl_test"

@pytest.fixture(scope="module", autouse=True)
def setup_dummy_interface():
    """Fixture to dynamically spin up and clean a virtual network interface for safe traffic shaping tests."""
    # Ensure root access
    if os.geteuid() != 0:
        pytest.skip("Skipping traffic shaper tests: root privileges required.")
        
    # Remove existing dummy if left over
    subprocess.run(["ip", "link", "del", TEST_IFACE], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Create dummy link
    res = subprocess.run(["ip", "link", "add", TEST_IFACE, "type", "dummy"], capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip(f"Failed to create dummy interface (check kernel dummy module): {res.stderr}")
        
    subprocess.run(["ip", "link", "set", TEST_IFACE, "up"], check=True)
    yield
    # Teardown
    subprocess.run(["ip", "link", "del", TEST_IFACE], check=False)

def test_traffic_shaper_rules():
    shaper = TrafficShaper(interface=TEST_IFACE)
    
    # 1. Initialize qdiscs
    shaper.init_qdiscs()
    
    # Verify qdisc exists
    res = subprocess.run(["tc", "qdisc", "show", "dev", TEST_IFACE], capture_output=True, text=True)
    assert "htb" in res.stdout
    assert "ingress" in res.stdout
    
    # 2. Add global limits
    shaper.set_global_limits(limit_down="10mbit", limit_up="2mbit")
    
    # 3. Add dynamic class limit
    class_id = shaper.add_throttle_class("test_cgroup", rate_down="5mbit", rate_up="1mbit")
    assert class_id >= 10
    
    # Verify class exists
    res_class = subprocess.run(["tc", "class", "show", "dev", TEST_IFACE], capture_output=True, text=True)
    assert f"1:{class_id}" in res_class.stdout
    
    # 4. Add dynamic port filter (port 4433 = hex 1151)
    shaper.add_inbound_port_filter(port=4433, class_id=class_id)
    assert (4433, class_id) in shaper.active_port_filters
    
    # Verify filter exists directly on the dummy interface ingress parent ffff:
    res_filt = subprocess.run(["tc", "filter", "show", "dev", TEST_IFACE, "parent", "ffff:"], capture_output=True, text=True)
    # Check for hex representation 1151 or decimal 4433
    assert "1151" in res_filt.stdout or "4433" in res_filt.stdout
    
    # 5. Remove port filter
    shaper.remove_inbound_port_filter(port=4433)
    assert (4433, class_id) not in shaper.active_port_filters
    
    # 6. Update class rate limits
    shaper.update_throttle_class(class_id, rate_down="2mbit", rate_up="500kbit")
    
    # 7. Remove throttle class
    shaper.remove_throttle_class("test_cgroup")
    
    # 8. Clean all
    shaper.cleanup()
