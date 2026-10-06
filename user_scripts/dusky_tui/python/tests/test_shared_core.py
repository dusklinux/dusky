"""Shared router and rendering regressions; no real config writes or editors."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.frontend import core_types, ui
from python.frontend.core_types import ConfigItem
from textual.app import App

LAUNCHER = Path(__file__).resolve().parents[1] / "main/main.py"
spec = importlib.util.spec_from_file_location("dusky_router_tests", LAUNCHER)
router = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = router
spec.loader.exec_module(router)


class PoolTests(unittest.TestCase):
    def test_get_propagates_registered_factory_errors_and_allows_retry(self):
        instance = object()
        factory = Mock(side_effect=[KeyError("broken engine configuration"), instance])
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        with self.assertRaisesRegex(KeyError, "broken engine configuration"):
            pool.get(key)
        self.assertIn(key, pool)
        self.assertEqual(list(pool.initialized_values()), [])
        self.assertIs(pool.get(key), instance)
        self.assertEqual(factory.call_count, 2)

    def test_registration_and_mapping_operations_do_not_construct_unused_engines(self):
        factory = Mock(return_value=object())
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        self.assertEqual(list(pool.keys()), [key])
        self.assertEqual(len(pool), 1)
        self.assertIn(key, pool)
        self.assertEqual(list(pool.initialized_values()), [])
        self.assertIsNone(pool.get(("unknown", "")))
        factory.assert_not_called()
        with self.assertRaises(KeyError):
            pool[("unknown", "")]
        self.assertIs(dict(pool)[key], factory.return_value)
        self.assertIs(pool[key], factory.return_value)
        factory.assert_called_once_with(*key)
        del pool[key]
        self.assertNotIn(key, pool)
        pool.register(*key)
        pool.clear()
        self.assertEqual(len(pool), 0)
        self.assertEqual(list(pool), [])
        factory.assert_called_once()

    def test_concurrent_lookup_constructs_once(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def factory(*_):
            calls.append(object())
            started.set()
            if not release.wait(5):
                raise TimeoutError("factory never released")
            return calls[-1]
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(pool.__getitem__, key)
            self.assertTrue(started.wait(5))
            second = executor.submit(pool.__getitem__, key)
            release.set()
            self.assertIs(first.result(5), second.result(5))
        self.assertEqual(len(calls), 1)


class PoolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovered_unit_reads_include_retained_rows_and_isolate_targets(self):
        class Engine:
            target_path = ""
            def __init__(self):
                self.calls = []
            def load_state_for_units(self, user, system):
                self.calls.append((user, system))
                return {f"{scope}/{unit}": "true" for scope, units in (("user", user), ("system", system))
                        for unit in units}
            def load_state(self):
                raise AssertionError("unnecessary full unit scan")
        pool = router.LazyEnginePool(lambda *_: Engine())
        first = pool.register("fixture", "/first")
        retained = ConfigItem(label="Retained", key="first.service", scope="user", type_="bool", default=False)
        local = ConfigItem(label="Local", key="new.service", scope="user", type_="bool", default=False)
        remote = ConfigItem(label="Remote", key="second.service", scope="system", type_="bool", default=False,
                            target_file_override="/second")
        app = ui.DuskyTUI(pool, first, {0: [retained], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False, deferred_load=lambda: ([1], {1: [local, remote]}))
        pool.bind_app(app)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not remote._initial_loaded:
                    await pilot.pause(.01)
            self.assertEqual(pool[first].calls[-1], (["first.service", "new.service"], []))
            self.assertEqual(pool[("fixture", "/second")].calls[-1], ([], ["second.service"]))
            self.assertIn("user/first.service", app._states[first])
            self.assertTrue(local.value)
            self.assertTrue(remote.value)

    async def test_unit_reads_are_isolated_by_engine_and_target(self):
        class Engine:
            def load_state_for_units(self, user, system):
                return {"user": user, "system": system}
        first, second = ("systemd", "/first"), ("systemd", "/second")
        items = [ConfigItem(label="First", key="first.service", scope="user", type_="bool", default=False),
                 ConfigItem(label="Second", key="second.service", scope="system", type_="bool", default=False,
                            target_file_override="/second")]
        app = ui.DuskyTUI({first: Engine(), second: Engine()}, first, {0: items}, ["Units"],
                          enable_user_presets=False, deferred_load=lambda: [])
        self.assertEqual(app._load_one_engine_sync(first), {"user": ["first.service"], "system": []})
        self.assertEqual(app._load_one_engine_sync(second), {"user": [], "system": ["second.service"]})

    async def test_discovery_waits_for_save_callbacks_before_publishing_state(self):
        class Engine:
            target_path = ""
            state = {}
            def load_state(self):
                return self.state.copy()
        key = ("fixture", "")
        engine = Engine()
        discovered = ConfigItem(label="New", key="y", type_="int", default=0)
        app = ui.DuskyTUI({key: engine}, key, {0: [], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False)
        release = asyncio.Event()
        async def finish_save():
            await release.wait()
            engine.state["y"] = "9"
            app._bump_write_generation("fixture-save")
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not app._boot_complete:
                    await pilot.pause(.01)
            app.deferred_load = lambda: ([1], {1: [discovered]}, {"y": "7"})
            app._deferred_started = True
            save = app._start_save_task(finish_save())
            worker = app._run_deferred_load(manual_refresh=True)
            try:
                await pilot.pause(.1)
                self.assertTrue(app._inventory_refreshing)
                self.assertEqual(app.schema[1], [])
                self.assertNotIn("y", app._states[key])
            finally:
                release.set()
                await save
            async with asyncio.timeout(5):
                await worker.wait()
            self.assertEqual(discovered.value, 9)
            self.assertEqual(app._states[key], {"y": "9"})
            self.assertFalse(app._inventory_refreshing)

    async def test_discovery_registers_and_loads_new_target(self):
        class Engine:
            target_path = ""
            def __init__(self, path):
                self.path = path
            def load_state(self):
                return {"y": "7"} if self.path == "/extra" else {}
        pool = router.LazyEnginePool(lambda _kind, path: Engine(path))
        key = pool.register("fixture", "")
        discovered = ConfigItem(label="New", key="y", type_="int", default=0,
                                target_file_override="/extra")
        app = ui.DuskyTUI(pool, key, {0: [], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False, deferred_load=lambda: ([1], {1: [discovered]}))
        pool.bind_app(app)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not discovered._initial_loaded:
                    await pilot.pause(.01)
            self.assertEqual(discovered.value, 7)
            self.assertIn(("fixture", "/extra"), app._loaded_engines)
            self.assertEqual(app._states[("fixture", "/extra")], {"y": "7"})
            self.assertTrue(app.require_boot_complete())

    async def test_worker_creation_stays_off_ui_and_binding_runs_on_ui_thread(self):
        owner = threading.get_ident()
        calls = []
        class Engine:
            def set_app(self, app):
                calls.append(("bind", threading.get_ident()))
        def factory(*_):
            calls.append(("create", threading.get_ident()))
            return Engine()
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        app = App()
        async with app.run_test():
            pool.bind_app(app)
            engine = await asyncio.to_thread(pool.__getitem__, key)
            self.assertIs(pool[key], engine)
        self.assertEqual([kind for kind, _thread in calls], ["create", "bind"])
        self.assertNotEqual(calls[0][1], owner)
        self.assertEqual(calls[1][1], owner)

    async def test_shutdown_does_not_construct_unused_backend(self):
        class Engine:
            target_path = ""
            shutdown = Mock()
            def load_state(self):
                return {}
        factory = Mock(side_effect=lambda *_: Engine())
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Initial"], enable_user_presets=False)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not app._boot_complete:
                    await pilot.pause(.01)
            pool.register("unused", "/fixture")
            pool.bind_app(app)
        factory.assert_called_once_with(*key)
        pool[key].shutdown.assert_called_once()


class UtilityTests(unittest.TestCase):
    def test_clone_preserves_custom_container_and_scalar_types(self):
        class CustomList(list):
            pass
        class CustomInt(int):
            pass
        for value in (CustomList([1, [2]]), CustomInt(3)):
            value.metadata = ["original"]
            copied = core_types.clone_value(value)
            self.assertIs(type(copied), type(value))
            self.assertIsNot(copied, value)
            copied.metadata.append("changed")
            self.assertEqual(value.metadata, ["original"])

    def test_hsl_overflow_returns_neutral_color(self):
        self.assertEqual(ui.color_to_rgb(f"hsl({'9' * 1000}, 50%, 50%)"), (128, 128, 128))

    def test_rgb_clamps_oversized_components_without_integer_conversion_failure(self):
        self.assertEqual(ui.color_to_rgb(f"rgb({'9' * 5000}, 0, 0)"), (255, 0, 0))
        self.assertEqual(ui.color_to_rgb(f"rgb({'0' * 5000}1, 2, 3)"), (1, 2, 3))
        self.assertEqual(ui.color_to_rgb("rgb(٠٠٠١, ٢, ٣)"), (1, 2, 3))

    def test_cached_option_tracks_read_only_changes(self):
        setting = ConfigItem(label="Value", key="value", type_="int", default=1)
        app = ui.DuskyTUI({}, ("ini", ""), {0: [setting]}, ["Initial"], enable_user_presets=False)
        self.assertNotIn("Read only", app._build_option(setting).plain)
        setting.read_only = True
        self.assertIn("Read only", app._build_option(setting).plain)

    def test_known_color_does_not_load_optional_database(self):
        core_types.is_theme_variable.cache_clear()
        with patch.object(core_types, "_get_css_named", side_effect=AssertionError("unnecessary import")):
            self.assertFalse(core_types.is_theme_variable("Red"))

    def test_special_css_colors_without_webcolors(self):
        original_import = __import__
        def without_webcolors(name, *args, **kwargs):
            if name == "webcolors":
                raise ModuleNotFoundError(name)
            return original_import(name, *args, **kwargs)
        core_types.is_theme_variable.cache_clear()
        with patch.object(core_types, "_css_named_cache", None), patch("builtins.__import__", side_effect=without_webcolors):
            for color in ("rebeccapurple", "transparent"):
                self.assertFalse(core_types.is_theme_variable(color))
        core_types.is_theme_variable.cache_clear()

    def test_hex_lengths_and_single_quote_character(self):
        self.assertTrue(core_types.is_theme_variable("0x1234567"))
        self.assertFalse(core_types.is_theme_variable("0x123456"))
        self.assertFalse(core_types.is_theme_variable("0x12345678"))
        setting = ConfigItem(label="Text", key="text", type_="string", default="")
        self.assertEqual(setting.deserialize('"'), '"')
        self.assertEqual(setting.deserialize('""'), "")

    def test_external_editor_routes_buttons_and_quoted_arguments(self):
        app = ui.DuskyTUI({}, ("ini", ""), {0: []}, ["Initial"], enable_user_presets=False)
        app.notify_status = Mock()
        app.run_suspended_interactive = Mock()
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "file with spaces.ini"
            file.touch()
            with patch.object(ui.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(ui.subprocess, "Popen") as popen:
                app.open_file_externally(file, button=1)
                popen.assert_called_once()
                self.assertEqual(popen.call_args.args[0], ["xdg-open", str(file)])
                app.run_suspended_interactive.assert_not_called()
            with patch.dict(os.environ, {"VISUAL": "editor --flag 'two words'"}):
                app.open_file_externally(file, button=3)
                app.run_suspended_interactive.assert_called_once_with(["editor", "--flag", "two words", str(file)])
        app.notify_status.assert_not_called()

    def test_cache_honors_interpreter_options(self):
        for options in (["-B"], ["-X", "pycache_prefix=/tmp/dusky-audit-explicit-cache"]):
            result = subprocess.run(
                [sys.executable, *options, "-c", "import runpy,sys; before=(sys.dont_write_bytecode,sys.pycache_prefix); runpy.run_path(sys.argv[1]); assert before==(sys.dont_write_bytecode,sys.pycache_prefix)", str(LAUNCHER)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_escalation_notice_does_not_contaminate_export_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "root_schema.py"
            schema.write_text("SCHEMA={0:[]}\nTABS=['Initial']\nTARGET_FILE='/tmp/dusky-root-fixture'\n"
                              "ENGINE_TYPE='ini'\nREQUIRE_ROOT=True\n", encoding="utf-8")
            code = (
                "import runpy,sys; from unittest.mock import patch; sys.argv=sys.argv[1:]\n"
                "with patch('os.geteuid',return_value=1000),patch('shutil.which',return_value='/usr/bin/sudo'),"
                "patch('os.execvp',side_effect=SystemExit(0)):\n"
                " runpy.run_path(sys.argv[0],run_name='__main__')\n"
            )
            result = subprocess.run([sys.executable, "-u", "-c", code, str(LAUNCHER), str(schema), "--export-state"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("Escalating", result.stderr)

    def test_failed_headless_batch_is_not_replayed_as_individual_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, calls, schema = (root / name for name in ("config.ini", "calls", "schema.py"))
            target.write_text("[main]\nvalue=1\n", encoding="utf-8")
            for message in ("Partial batch failure", "AUTH_REQUIRED"):
                with self.subTest(message=message):
                    calls.unlink(missing_ok=True)
                    schema.write_text(
                        "from pathlib import Path\n"
                        "from python.frontend.core_types import ConfigItem\n"
                        "from python.engines.ini import IniConfigEngine\n"
                        f"calls=Path({str(calls)!r})\n"
                        "def batch(self, changes):\n"
                        "    calls.write_text('batch\\n')\n"
                        f"    return False, {message!r}, ''\n"
                        "def single(self, *args, **kwargs):\n"
                        "    with calls.open('a') as out: out.write('replayed\\n')\n"
                        "    return True, '', ''\n"
                        "IniConfigEngine.write_batch=batch\nIniConfigEngine.write_value=single\n"
                        f"TARGET_FILE={str(target)!r}\nENGINE_TYPE='ini'\nTABS=['Values']\n"
                        "SCHEMA={0:[ConfigItem(label='Value', key='value', scope='main', type_='int', default=2)]}\n",
                        encoding="utf-8",
                    )
                    result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), "--default"],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                    self.assertEqual(calls.read_text(), "batch\n")
                    self.assertIn(message, result.stdout)

    def test_headless_router_round_trip_across_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, schema = (root / name for name in ("first.ini", "second.ini", "schema.py"))
            first.write_text("[main]\ncount=1\nflag=false\nreadonly=7\n", encoding="utf-8")
            second.write_text("[main]\nother=10\n", encoding="utf-8")
            schema.write_text(
                "from python.frontend.core_types import ConfigItem\n"
                f"TARGET_FILE={str(first)!r}\nENGINE_TYPE='ini'\nENABLE_USER_PRESETS=False\n"
                "TABS=['Values']\nSCHEMA={0:[\n"
                "ConfigItem(label='Count', key='count', scope='main', type_='int', default=2),\n"
                "ConfigItem(label='Flag', key='flag', scope='main', type_='bool', default=True),\n"
                "ConfigItem(label='Readonly', key='readonly', scope='main', type_='int', default=0, read_only=True),\n"
                f"ConfigItem(label='Other', key='other', scope='main', type_='int', default=9, target_file_override={str(second)!r}),\n"
                "]}\n", encoding="utf-8",
            )
            def invoke(*args):
                result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), *args],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout
            self.assertIn("Read only", invoke("--export-docs"))
            invoke("--set", "main.count=3")
            self.assertIn("count=3", first.read_text(encoding="utf-8"))
            invoke("--set", "flag=off")
            self.assertIn("flag=false", first.read_text(encoding="utf-8"))
            invoke("--reset-key", "count")
            self.assertIn("count=2", first.read_text(encoding="utf-8"))
            invoke("--default")
            self.assertIn("readonly=7", first.read_text(encoding="utf-8"))
            self.assertIn("other=9", second.read_text(encoding="utf-8"))
            import json
            state = json.loads(invoke("--export-state"))
            self.assertEqual(state["main/count"], "2")
            self.assertEqual(state["main/flag"], "true")
            self.assertEqual(next(value for key, value in state.items() if key.endswith("::main/other")), "9")
            for flag in ("--set", "--reset-key"):
                result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), flag, ""],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
