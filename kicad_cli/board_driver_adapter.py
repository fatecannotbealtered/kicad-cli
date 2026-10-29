"""Route real board tasks to adapters; legacy handlers are never wrapped."""

from . import engineering_observation
from .engineering_driver_adapter import COMMANDS as ENGINEERING_COMMANDS
from .engineering_driver_adapter import EngineeringAdapter
from .export_driver_adapter import COMMANDS, ExportAdapter
from .move_driver_adapter import MoveAdapter
from .power_driver_adapter import COMMANDS as POWER_COMMANDS
from .power_driver_adapter import PowerAdapter
from .read_driver_adapter import ReadAdapter
from .sync_driver_adapter import SyncAdapter
from .zone_driver_adapter import ZoneAdapter


class BoardAdapter:
    def __init__(self, validator):
        self.move, self.zone = MoveAdapter(validator), ZoneAdapter(validator)
        self.sync = SyncAdapter(validator)
        self.read = ReadAdapter()
        self.export = ExportAdapter(validator)
        self.power = PowerAdapter(validator)
        self.engineering = EngineeringAdapter(validator)

    def run(self, call):
        from .module_contract import READ_COMMANDS

        if call.command in READ_COMMANDS:
            from .module_operations import run

            return run(call)
        if call.command in engineering_observation.COMMANDS:
            return engineering_observation.run(call)
        return self.read.run(call)

    environment = staticmethod(engineering_observation.environment)
    doctor_checks = staticmethod(engineering_observation.doctor_checks)

    @property
    def scope_provider(self):
        return self.move.scope_provider

    @scope_provider.setter
    def scope_provider(self, provider):
        self.move.scope_provider = self.zone.scope_provider = self.sync.scope_provider = (
            self.export.scope_provider
        ) = self.power.scope_provider = self.engineering.scope_provider = provider

    def _for(self, command):
        if command in ENGINEERING_COMMANDS:
            return self.engineering
        if command in POWER_COMMANDS:
            return self.power
        if command in COMMANDS:
            return self.export
        if command == "project sync":
            return self.sync
        return self.zone if command in {"board zone create", "board zone refill"} else self.move

    def revision(self, target):
        if target.get("engine_id") == "kicad-cli.saved-board":
            import os
            from urllib.parse import unquote, urlparse

            from .document_transaction import revision

            path = unquote(urlparse(target["locator"]).path)
            return revision(path.lstrip("/") if os.name == "nt" else path)
        return self.move.revision(target)

    def plan(self, call):
        return self._for(call.command).plan(call)

    def apply(self, call, plan):
        return self._for(call.command).apply(call, plan)

    def recovery(self, record):
        return self._for(record["command"]).recovery(record)

    def validate_plan(self, record):
        if record["command"] == "board footprint move":
            self.move.validate_plan(record)
        elif record["command"] in ENGINEERING_COMMANDS:
            self.engineering.validate_plan(record)
        elif record["command"] in POWER_COMMANDS:
            self.power.validate_plan(record)
        elif record["command"] in {"board zone create", "board zone refill"}:
            self.zone.validate_plan(record)
        elif record["command"] == "project sync":
            self.sync.validate_plan(record)
        elif record["command"] in COMMANDS:
            self.export.validate_plan(record)
