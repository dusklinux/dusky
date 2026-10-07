"""Work-count and lifecycle regressions for deferred custom views."""
import asyncio
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.ufw import UfwEngine, RuleRecord, COMMON_SERVICES
from python.frontend.ui import CustomRichTabWidget, DuskyTUI, FlowContainer, ConfigOptionList
from python.frontend.core_types import ConfigItem
from textual.widgets import Static, Tabs


class Engine:
    target_path = ""
    def load_state(self):
        return {}


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_and_discards_hidden_result_without_blocking_ui(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        rendered = []
        ui_thread = threading.get_ident()
        def collect(prepared):
            calls.append(threading.get_ident())
            started.set()
            release.wait(10)
            return prepared
        app = SimpleNamespace(_save_lock=asyncio.Lock(), _custom_refresh_tasks=set())
        app._run_save_io = lambda *args: DuskyTUI._run_save_io(app, *args)
        widget = CustomRichTabWidget(lambda result: result, app_ref=app,
                                     collector=collect, prepare=lambda _: "snapshot")
        widget._apply_rendered_content = rendered.append
        widget.set_active(True)
        try:
            async with asyncio.timeout(10):
                while not started.is_set():
                    await asyncio.sleep(.01)
            # The event loop processed this while the collector remains blocked.
            self.assertNotEqual(calls[0], ui_thread)
            widget.update_content()
            widget.update_content()
            widget.set_active(False)
            release.set()
            await widget._refresh_task
            self.assertEqual(len(calls), 1)
            self.assertEqual(rendered, [])
            widget.set_active(True)
            await widget._refresh_task
            self.assertEqual(rendered, ["snapshot"])
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
            widget.set_active(False)
            if widget._refresh_task:
                await widget._refresh_task

    async def test_cancellation_drains_collector_before_releasing_engine_lock(self):
        started = threading.Event()
        release = threading.Event()
        rendered = []
        def collect(_):
            started.set()
            release.wait(10)
            return "late"
        app = SimpleNamespace(_save_lock=asyncio.Lock(), _custom_refresh_tasks=set())
        app._run_save_io = lambda *args: DuskyTUI._run_save_io(app, *args)
        widget = CustomRichTabWidget(lambda result: result, app_ref=app, collector=collect)
        widget._apply_rendered_content = rendered.append
        widget.set_active(True)
        task = widget._refresh_task
        try:
            async with asyncio.timeout(10):
                while not started.is_set():
                    await asyncio.sleep(.01)
            widget.on_unmount()
            await asyncio.sleep(.01)
            self.assertTrue(app._save_lock.locked())
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(app._save_lock.locked())
            self.assertEqual(rendered, [])
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_active_refresh_requests_coalesce_into_one_fresh_result(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        rendered = []
        def collect(_):
            calls.append(len(calls) + 1)
            started.set()
            release.wait(10)
            return calls[-1]
        app = SimpleNamespace(_save_lock=asyncio.Lock(), _custom_refresh_tasks=set())
        app._run_save_io = lambda *args: DuskyTUI._run_save_io(app, *args)
        widget = CustomRichTabWidget(lambda result: result, app_ref=app, collector=collect)
        widget._apply_rendered_content = rendered.append
        widget.set_active(True)
        task = widget._refresh_task
        try:
            async with asyncio.timeout(10):
                while not started.is_set():
                    await asyncio.sleep(.01)
            for _ in range(5):
                widget.update_content()
            self.assertIs(widget._refresh_task, task)
            release.set()
            await task
            self.assertEqual(calls, [1, 2])
            self.assertEqual(rendered, [2])
        finally:
            release.set()
            widget.set_active(False)
            await asyncio.gather(task, return_exceptions=True)

    async def test_clean_snapshot_survives_switch_and_invalidated_snapshot_recollects(self):
        calls = []
        app = SimpleNamespace(_save_lock=asyncio.Lock(), _custom_refresh_tasks=set())
        app._run_save_io = lambda *args: DuskyTUI._run_save_io(app, *args)
        def collect(_):
            calls.append(1)
            return "snapshot"
        widget = CustomRichTabWidget(lambda result: result, app_ref=app, collector=collect)
        widget._apply_rendered_content = lambda _: None
        widget.set_active(True)
        await widget._refresh_task
        self.assertEqual(len(calls), 1)
        widget.set_active(False)
        widget.set_active(True)
        self.assertIsNone(widget._refresh_task)
        self.assertEqual(len(calls), 1)
        widget.set_active(False)
        widget.invalidate_content()
        widget.set_active(True)
        await widget._refresh_task
        self.assertEqual(len(calls), 2)
        widget.set_active(False)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_lazy_list_creation_is_retryable(self):
        key = ("fixture", "")
        app = DuskyTUI(engine_pool={key: Engine()}, default_engine_key=key,
                       schema={0: [], 1: []}, tabs=["First", "Hidden"],
                       enable_user_presets=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertFalse(app.query("#list-1"))
            with patch.object(app, "_option_list_widgets", side_effect=RuntimeError("List fixture")):
                with self.assertRaisesRegex(RuntimeError, "List fixture"):
                    await app._ensure_option_list(1)
            self.assertFalse(app._option_mount_tasks)
            host = app.query_one("#list-wrapper-1")
            mount = host.mount
            async def failed_mount(*nodes):
                await mount(*nodes)
                raise RuntimeError("Partial mount fixture")
            with patch.object(host, "mount", side_effect=failed_mount):
                with self.assertRaisesRegex(RuntimeError, "Partial mount fixture"):
                    await app._ensure_option_list(1)
            self.assertFalse(host.children)
            self.assertFalse(app._option_mount_tasks)
            await asyncio.gather(app._ensure_option_list(1), app._ensure_option_list(1))
            app.action_switch_tab(1)
            await pilot.pause()
            self.assertEqual(len(app.query("#list-1")), 1)
            self.assertFalse(app._option_mount_tasks)

    async def activated(self, app, pilot, tab):
        async with asyncio.timeout(15):
            while app._current_tab_index() != tab:
                await pilot.pause(.05)
        await pilot.pause()

    async def test_hidden_views_are_lazy_stop_polling_and_mount_once(self):
        calls = [0, 0]
        def view(index):
            def render():
                calls[index] += 1
                return f"View {index}"
            return {"view": render, "interval": .05}
        key = ("fixture", "")
        app = DuskyTUI(engine_pool={key: Engine()}, default_engine_key=key,
                       schema={0: [], 3: []}, tabs={0: "First", 3: "Hidden"},
                       custom_views={0: view(0), 3: view(1)}, enable_user_presets=False,
                       tab_notices={3: [{"message": "Top"}, {"message": "Bottom", "position": "bottom"}]})
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            self.assertEqual(calls[1], 0)
            self.assertEqual(app._mounted_tabs, {0})
            self.assertFalse(app.query("#notice-3-0"))
            app.action_switch_tab(3)
            await self.activated(app, pilot, 3)
            self.assertGreater(calls[1], 0)
            pane = app.query_one("#tab-3")
            self.assertEqual([child.id for child in pane.children],
                             ["notice-3-0", "custom-body-3", "notice-3-1-bot"])
            self.assertEqual(len(app.query_one("#custom-body-3").children), 1)
            app.action_switch_tab(0)
            await self.activated(app, pilot, 0)
            hidden_calls = calls[1]
            app._refresh_custom_views()
            await pilot.pause(.2)
            self.assertEqual(calls[1], hidden_calls)
            # Repeated requests share one mount and do not duplicate children.
            await asyncio.gather(app._ensure_custom_body(3), app._ensure_custom_body(3))
            app.action_switch_tab(3)
            app.action_switch_tab(0)
            app.action_switch_tab(3)
            await self.activated(app, pilot, 3)
            self.assertEqual(app._current_tab_index(), 3)
            self.assertEqual(len(app.query_one("#custom-body-3").children), 1)
            self.assertFalse(app.query_one("#custom-view-0")._active)
            self.assertTrue(app.query_one("#custom-view-3")._active)

    async def test_widget_variants_and_footer_wrap(self):
        key = ("fixture", "")
        supplied = Static("Supplied", id="supplied")
        app = DuskyTUI(engine_pool={key: Engine()}, default_engine_key=key,
                       schema={0: [], 1: [], 2: []}, tabs=["First", "Class", "Instance"],
                       custom_views={1: Static, 2: supplied}, enable_user_presets=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertIsNone(supplied.parent)
            for tab in (1, 2):
                app.action_switch_tab(tab)
                await pilot.pause()
                self.assertIn(tab, app._mounted_tabs)
            self.assertIs(app.query_one("#supplied"), supplied)
            for width in (48, 80, 120):
                await pilot.resize_terminal(width, 24)
                await pilot.pause()
                footer = app.query_one(FlowContainer)
                children = [child for child in footer.children if child.display]
                for index, child in enumerate(children):
                    self.assertGreater(child.region.width, 0)
                    self.assertLessEqual(child.region.right, footer.content_region.right)
                    self.assertLessEqual(child.region.bottom, footer.content_region.bottom)
                    for previous in children[:index]:
                        self.assertFalse(child.region.overlaps(previous.region))

    async def test_search_mounts_mixed_tab_before_focusing_result(self):
        key = ("fixture", "")
        item = ConfigItem(label="Target", key="target", type_="bool", default=False)
        app = DuskyTUI(engine_pool={key: Engine()}, default_engine_key=key,
                       schema={0: [], 3: [item]}, tabs={0: "First", 3: "Mixed"},
                       custom_views={3: {"view": lambda: "Body", "show_options": True}},
                       enable_user_presets=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_search()
            await pilot.pause()
            app.screen.dismiss((3, 0))
            await self.activated(app, pilot, 3)
            self.assertEqual(app._current_tab_index(), 3)
            self.assertIn(3, app._mounted_tabs)
            options = app.query_one("#list-3", ConfigOptionList)
            self.assertIs(app.focused, options)
            self.assertEqual(options.get_option_at_index(options.highlighted).id, "item_3_0")
            self.assertIsNone(app._pending_search_target)

    async def test_failed_body_creation_is_retryable(self):
        key = ("fixture", "")
        app = DuskyTUI(engine_pool={key: Engine()}, default_engine_key=key,
                       schema={0: [], 1: []}, tabs=["First", "Custom"],
                       custom_views={1: lambda: "Body"}, enable_user_presets=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app, "_custom_body_widgets", side_effect=RuntimeError("Mount fixture")):
                with self.assertRaisesRegex(RuntimeError, "Mount fixture"):
                    await app._ensure_custom_body(1)
            self.assertNotIn(1, app._mounted_tabs)
            self.assertFalse(app._custom_mount_tasks)
            await app._ensure_custom_body(1)
            app.action_switch_tab(1)
            await pilot.pause()
            self.assertIn(1, app._mounted_tabs)
            self.assertEqual(len(app.query_one("#custom-body-1").children), 1)


    async def test_failed_initial_engine_never_collects_custom_data(self):
        class BrokenEngine(Engine):
            def load_state(self):
                raise RuntimeError("Load fixture")
        calls = []
        key = ("fixture", "")
        app = DuskyTUI(engine_pool={key: BrokenEngine()}, default_engine_key=key,
                       schema={0: []}, tabs=["Custom"], custom_views={0: lambda: calls.append(1)},
                       enable_user_presets=False)
        async with app.run_test() as pilot:
            async with asyncio.timeout(10):
                while not app._boot_complete:
                    await pilot.pause(.05)
            self.assertIn(key, app._failed_engines)
            self.assertEqual(calls, [])
            self.assertFalse(app.query_one(CustomRichTabWidget)._active)


    async def test_model_change_invalidates_hidden_collector_snapshot(self):
        engine = Engine()
        engine.value = 0
        key = ("fixture", "")
        app = DuskyTUI(engine_pool={key: engine}, default_engine_key=key,
                       schema={0: [], 1: []}, tabs=["Snapshot", "Other"],
                       custom_views={0: {"view": lambda value: str(value),
                                         "prepare": lambda app: engine,
                                         "collect": lambda engine: engine.value}},
                       enable_user_presets=False)
        async with app.run_test() as pilot:
            view = app.query_one(CustomRichTabWidget)
            async with asyncio.timeout(10):
                while view._collected_snapshot is None:
                    await pilot.pause(.05)
            app.action_switch_tab(1)
            await self.activated(app, pilot, 1)
            engine.value = 1
            app._bump_write_generation("fixture-change")
            self.assertTrue(view._dirty)
            app.action_switch_tab(0)
            await self.activated(app, pilot, 0)
            async with asyncio.timeout(10):
                while view._dirty:
                    await pilot.pause(.05)
            self.assertEqual(view._collected_snapshot, 1)



class RuleSnapshotTests(unittest.TestCase):
    def test_load_reads_stored_rules_once_and_preserves_service_classification(self):
        engine = UfwEngine(config_path="/tmp/dusky-test-ufw-snapshot")
        commands = [["allow", "22/tcp", "comment", "dusky:service:ssh"]]
        with patch.object(engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             patch.object(engine, "_read_domain_registry", return_value={}), \
             patch.object(engine, "get_waydroid_nat", return_value=False), \
             patch.object(engine, "get_docker_mitigation", return_value=False), \
             patch.object(engine, "get_icmp_ping_stealth", return_value=False), \
             patch.object(engine, "_stored_rule_commands", return_value=commands) as read:
            expected = {name: "true" if engine.is_service_allowed(name, commands=commands) else "false"
                        for name in COMMON_SERVICES}
            state = engine.load_state()
            read.assert_called_once_with()
            self.assertEqual({name: state[f"services/{name}"] for name in COMMON_SERVICES}, expected)
            self.assertFalse(engine.is_service_allowed("ssh", commands=[]))
            read.assert_called_once_with()

    def test_banned_ip_classification_reuses_explicit_rules_even_when_empty(self):
        engine = UfwEngine(config_path="/tmp/dusky-test-ufw-snapshot")
        rules = [RuleRecord(number=1, to_addr="Anywhere", action="DENY IN",
                            from_addr="192.0.2.1", comment="Banned: fixture"),
                 RuleRecord(number=2, to_addr="Anywhere", action="DENY IN",
                            from_addr="192.0.2.1", comment="block: duplicate")]
        with patch.object(engine, "get_numbered_rules", return_value=rules) as read:
            self.assertEqual(engine.get_banned_ips(), ["192.0.2.1"])
            read.assert_called_once_with()
            read.reset_mock()
            self.assertEqual(engine.get_banned_ips(rules=rules), ["192.0.2.1"])
            self.assertEqual(engine.get_banned_ips(rules=[]), [])
            read.assert_not_called()


class UfwViewTests(unittest.TestCase):
    def test_collectors_read_once_and_reports_render_collected_content(self):
        import importlib.util
        schema_path = Path(__file__).resolve().parents[3] / "network_manager" / "tui_firewall.py"
        spec = importlib.util.spec_from_file_location("dusky_ufw_test_schema", schema_path)
        schema = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema)
        engine = Mock()
        engine.cache = {}
        engine.get_status_verbose.return_value = {}
        engine.get_numbered_rules.return_value = []
        engine.detect_wan_interface.return_value = None
        engine._read_domain_registry.return_value = {}
        engine.get_banned_ips.return_value = []
        engine.get_listening_ports.return_value = []
        engine.get_detailed_port_map.return_value = []
        engine.get_active_connections.return_value = []
        engine.get_report.return_value = "Collected report text"
        for kind, renderer in (
            ("status", schema.render_ufw_dashboard_view), ("ports", schema.render_ports_view),
            ("rules", schema.render_rules_view), ("domains", schema.render_domains_view),
            ("connections", schema.render_connections_view), ("reports", schema.render_reports_view),
        ):
            engine.reset_mock()
            snapshot = schema.collect_ufw_view((engine, kind, "listening"))
            read_calls = list(engine.mock_calls)
            result = renderer(snapshot)
            self.assertEqual(engine.mock_calls, read_calls, "Rendering must perform no engine I/O")
            self.assertEqual(len(read_calls), len(set(call[0] for call in read_calls)))
            if kind == "reports":
                self.assertEqual(result.renderable.plain, "Collected report text")
