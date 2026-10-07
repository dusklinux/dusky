"""Systemd unit-file discovery and enablement for the service TUI."""

import os
lazy import subprocess
from dataclasses import dataclass
lazy from typing import Any

from python.frontend.core_types import BaseEngine


READ_TIMEOUT = 10
WRITE_TIMEOUT = 120
ENABLED_STATES = frozenset({"enabled", "enabled-runtime"})
MANAGEABLE_STATES = frozenset({"enabled", "enabled-runtime", "disabled", "indirect"})


@dataclass(frozen=True)
class UnitWriteResult:
    ok: bool
    message: str
    actual: str | None


class SystemdEngine(BaseEngine):
    """Read unit-file state and change enablement through systemctl."""

    def __init__(self, config_path: str = ""):
        # Unit files live in several system and user directories. No single
        # directory mtime tracks their inventory or enablement reliably.
        self._target_path = ""

    @property
    def target_path(self) -> str:
        return self._target_path

    @staticmethod
    def _prefix(scope: str, write: bool = False) -> list[str]:
        if scope == "user":
            return ["systemctl", "--user"]
        if scope == "system":
            return ["sudo", "-n", "systemctl"] if write else ["systemctl"]
        raise ValueError(f"Invalid systemd scope: {scope!r}")

    @staticmethod
    def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=timeout, env={**os.environ, "LC_ALL": "C"},
        )

    @classmethod
    def list_unit_files(cls, scope: str, units: list[str] | None = None) -> dict[str, str]:
        """Return discoverable unit files; an empty targeted match is valid."""
        if units is not None and not units:
            return {}
        cmd = cls._prefix(scope) + [
            "list-unit-files", "--type=service,timer", "--no-pager", "--no-legend", "--full"
        ]
        if units is not None:
            cmd.extend(units)
        res = cls._run(cmd, READ_TIMEOUT)
        if res.returncode != 0 and not (
            units is not None and res.returncode == 1
            and not res.stdout.strip() and not res.stderr.strip()
        ):
            raise RuntimeError(res.stderr.strip() or f"systemctl exited with status {res.returncode}")
        states = {
            parts[0]: parts[1]
            for line in res.stdout.splitlines()
            if len(parts := line.split()) >= 2 and parts[1] != "not-found"
        }
        # list-unit-files lists templates, but may omit loaded instances.
        # Inspect requested concrete instances through the manager itself.
        instances = [
            unit for unit in units or ()
            if "@" in unit and "@." not in unit and unit not in states
        ]
        if instances:
            shown = cls._run(cls._prefix(scope) + [
                "show", *instances, "--property=Id,LoadState,UnitFileState,FragmentPath", "--no-pager"
            ], READ_TIMEOUT)
            if shown.returncode:
                raise RuntimeError(shown.stderr.strip() or f"systemctl show exited with status {shown.returncode}")
            for block in shown.stdout.strip().split("\n\n"):
                props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                if props.get("Id") in instances and props.get("LoadState") == "loaded" and props.get("FragmentPath"):
                    state = props.get("UnitFileState")
                    if state and state not in {"bad", "not-found"}:
                        states[props["Id"]] = state
        return states

    def load_state(self) -> dict[str, Any]:
        return {
            f"{scope}/{unit}": "true" if state in ENABLED_STATES else "false"
            for scope in ("user", "system")
            for unit, state in self.list_unit_files(scope).items()
        }

    def load_state_for_units(self, user_units: list[str], sys_units: list[str]) -> dict[str, Any]:
        state = {}
        for scope, units in (("user", user_units), ("system", sys_units)):
            for unit, unit_state in self.list_unit_files(scope, units).items():
                state[f"{scope}/{unit}"] = "true" if unit_state in ENABLED_STATES else "false"
        return state

    @staticmethod
    def _auth_required(stderr: str) -> bool:
        error = stderr.lower()
        return "sudo:" in error and (
            "password is required" in error
            or "a terminal is required" in error
            or "no tty present and no askpass program" in error
        )

    def write_value_result(self, key: str, scope: str, value: str) -> UnitWriteResult:
        """Return observed enablement even when the runtime action fails."""
        if value not in {"true", "false"}:
            return UnitWriteResult(False, f"Invalid systemd value: {value!r}", None)
        try:
            before = self.list_unit_files(scope, [key]).get(key)
            if before is None:
                return UnitWriteResult(False, f"Unit {key} is not installed", None)
            if before not in MANAGEABLE_STATES:
                return UnitWriteResult(False, f"Unit {key} has no ordinary enablement switch ({before})", "false")

            action = "enable" if value == "true" else "disable"
            res = self._run(self._prefix(scope, write=True) + [action, "--now", key], WRITE_TIMEOUT)
            error = res.stderr.strip()
            try:
                after = self.list_unit_files(scope, [key]).get(key)
                actual = ("true" if after in ENABLED_STATES else "false") if after else None
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                actual = None
            if res.returncode == 0 and actual in (None, value):
                return UnitWriteResult(True, f"{action.capitalize()}d {key}", actual)
            if res.returncode == 0:
                return UnitWriteResult(False, f"{action} --now {key} did not change enablement", actual)
            if scope == "system" and self._auth_required(error):
                return UnitWriteResult(False, "AUTH_REQUIRED", actual)
            if actual == value:
                runtime_action = "start" if value == "true" else "stop"
                return UnitWriteResult(False, f"{key} has the requested enablement, but {runtime_action} failed: {error or f'exit {res.returncode}'}", actual)
            return UnitWriteResult(False, f"{action} --now {key} failed: {error or f'exit {res.returncode}'}", actual)
        except subprocess.TimeoutExpired:
            try:
                after = self.list_unit_files(scope, [key]).get(key)
                actual = ("true" if after in ENABLED_STATES else "false") if after else None
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                actual = None
            detail = "enablement was rechecked" if actual is not None else "enablement could not be verified"
            return UnitWriteResult(False, f"Timed out changing {key}; {detail}", actual)
        except (OSError, RuntimeError, ValueError) as exc:
            try:
                after = self.list_unit_files(scope, [key]).get(key)
                actual = ("true" if after in ENABLED_STATES else "false") if after else None
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                actual = None
            return UnitWriteResult(False, f"Could not change {key}: {exc}", actual)

    def write_value(self, target_key: str, target_scope: str, new_value: str,
                    item_type: str = "bool") -> tuple[bool, str, str]:
        result = self.write_value_result(target_key, target_scope, new_value)
        return result.ok, result.message, ""

    def write_batch_results(self, changes: list[tuple[str, str, str, str]]) -> dict[tuple[str, str], UnitWriteResult]:
        """Group commands, then reconcile every unit without retrying writes."""
        requested = {}
        for key, scope, value, _ in changes:
            requested[(key, scope)] = value
        results = {}
        for key, scope in requested:
            if scope not in {"user", "system"}:
                results[(key, scope)] = UnitWriteResult(False, f"Invalid systemd scope: {scope!r}", None)
        for scope in ("user", "system"):
            units = [key for key, unit_scope in requested if unit_scope == scope]
            if not units:
                continue
            try:
                before = self.list_unit_files(scope, units)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                for key in units:
                    results[(key, scope)] = UnitWriteResult(False, f"Could not read {key}: {exc}", None)
                continue

            groups = {"enable": [], "disable": []}
            for key in units:
                value = requested[(key, scope)]
                state = before.get(key)
                if value not in {"true", "false"}:
                    results[(key, scope)] = UnitWriteResult(False, f"Invalid systemd value: {value!r}", None)
                elif state is None:
                    results[(key, scope)] = UnitWriteResult(False, f"Unit {key} is not installed", None)
                elif state not in MANAGEABLE_STATES:
                    results[(key, scope)] = UnitWriteResult(False, f"Unit {key} has no ordinary enablement switch ({state})", "false")
                else:
                    groups["enable" if value == "true" else "disable"].append(key)

            for action, group in groups.items():
                if not group:
                    continue
                try:
                    res = self._run(self._prefix(scope, write=True) + [action, "--now", *group], WRITE_TIMEOUT)
                    failed = res.returncode != 0
                    error = res.stderr.strip() or f"exit {res.returncode}"
                    auth = scope == "system" and self._auth_required(res.stderr)
                except subprocess.TimeoutExpired:
                    failed, auth, error = True, False, "timed out"
                except OSError as exc:
                    failed, auth, error = True, False, str(exc)

                try:
                    after = self.list_unit_files(scope, group)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    after = {}
                for key in group:
                    state = after.get(key)
                    actual = ("true" if state in ENABLED_STATES else "false") if state else None
                    desired = requested[(key, scope)]
                    if auth:
                        results[(key, scope)] = UnitWriteResult(False, "AUTH_REQUIRED", actual)
                    elif failed:
                        observed = (
                            "has the requested enablement" if actual == desired else
                            "enablement could not be verified" if actual is None else
                            "does not have the requested enablement"
                        )
                        message = f"{key} {observed}; {action} --now batch reported: {error}"
                        results[(key, scope)] = UnitWriteResult(False, message, actual)
                    elif actual is not None and actual != desired:
                        results[(key, scope)] = UnitWriteResult(False, f"{action} --now {key} did not change enablement", actual)
                    else:
                        results[(key, scope)] = UnitWriteResult(True, f"{action.capitalize()}d {key}", actual)
        return results

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        results = self.write_batch_results(changes)
        failures = [r.message for r in results.values() if not r.ok]
        if not failures:
            return True, f"Changed {len(results)} systemd units.", ""
        return False, failures[0], "\n".join(failures)
