"""Structural passive isolation: Monitor Mode has no automation service or input factory."""


class MonitorModeController:
    armed = False
    blocked = ""
    pending = None

    def __init__(self, notify):
        self.notify = notify

    def consume(self, report):
        self.notify(report)

    def arm(self):
        raise ValueError("Monitor Mode cannot arm automation.")

    def disarm(self, _reason=""):
        pass

    def close(self):
        pass

    def poll(self):
        pass


class OfflineModeController:
    def __init__(self, notify, profile, workspace, desktop, visual_verify, **kwargs):
        # Lazy import only after the user selects Offline Automation explicitly.
        from .automation import ActionController

        self.notify = notify
        self.actions = ActionController(profile, workspace, desktop, visual_verify, **kwargs)

    @property
    def armed(self):
        return self.actions.armed

    @property
    def blocked(self):
        return self.actions.blocked

    def consume(self, report):
        self.notify(report)
        self.actions.observe(report)

    def arm(self):
        self.actions.arm()

    def disarm(self, reason=""):
        self.actions.disarm(reason)

    def close(self):
        self.actions.close()

    def poll(self):
        self.actions.tick()
