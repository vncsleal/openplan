from __future__ import annotations


class OpenPlanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class InvalidStateError(OpenPlanError):
    def __init__(self, state_id: str) -> None:
        super().__init__("INVALID_STATE", f"State {state_id} not found")


class InvalidActionError(OpenPlanError):
    def __init__(self, state_id: str, action: str) -> None:
        super().__init__("INVALID_ACTION", f"No edge from {state_id} with action '{action}'")


class TargetNotFoundError(OpenPlanError):
    def __init__(self, state_id: str, action: str, target: str) -> None:
        super().__init__("TARGET_NOT_FOUND", f"No edge from {state_id} with action '{action}' targeting '{target}'")


class CycleDetectedError(OpenPlanError):
    def __init__(self, state_id: str, target_id: str) -> None:
        super().__init__("CYCLE_DETECTED", f"Acting {state_id} -> {target_id} would create a cycle")


class InvalidOutcomeError(OpenPlanError):
    def __init__(self, outcome: str) -> None:
        super().__init__("INVALID_OUTCOME", f"Expected 'success', 'partial', or 'failure', got '{outcome}'")


class NoEventError(OpenPlanError):
    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__("NO_EVENT", f"No acted event found from {from_state} to {to_state}")


class NoActionError(OpenPlanError):
    def __init__(self) -> None:
        super().__init__("NO_ACTION", "Event payload missing action")


class InvalidPayloadError(OpenPlanError):
    def __init__(self) -> None:
        super().__init__("INVALID_PAYLOAD", "Event payload is not valid JSON")


class NoEdgeError(OpenPlanError):
    def __init__(self, from_state: str, to_state: str, action: str) -> None:
        super().__init__("NO_EDGE", f"No edge from {from_state} to {to_state} with action '{action}'")


class NoPathError(OpenPlanError):
    def __init__(self) -> None:
        super().__init__("NO_PATH", "No path found from source to target")


class TargetResolutionError(OpenPlanError):
    def __init__(self, message: str) -> None:
        super().__init__("TARGET_RESOLUTION_FAILED", message)


class NoOptionsError(OpenPlanError):
    def __init__(self) -> None:
        super().__init__("NO_OPTIONS", "At least one option required")


class InvalidStatusError(OpenPlanError):
    def __init__(self, status: str) -> None:
        super().__init__("INVALID_STATUS", f"Invalid status value: '{status}'. Must be one of: pending, in_progress, done, blocked, superseded")


class PreconditionError(OpenPlanError):
    def __init__(self, state_id: str, action: str, precondition: str) -> None:
        super().__init__("PRECONDITION_FAILED", f"Cannot {action} from {state_id}: precondition '{precondition}' not satisfied")


class TerminalStateError(OpenPlanError):
    def __init__(self, state_id: str) -> None:
        super().__init__("TERMINAL_STATE", f"State {state_id} is terminal and cannot transition further")


class GoalNotFoundError(OpenPlanError):
    def __init__(self, goal: str, project: str) -> None:
        super().__init__("GOAL_NOT_FOUND", f"Goal '{goal}' not found in project '{project}'")
