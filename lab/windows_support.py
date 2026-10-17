"""Narrow Windows path adapter for HA's POSIX-only frame attribution.

This preserves real integration attribution, rather than suppressing HA's
checks. Linux/Container does not use this adapter. No files in HA are changed.
"""
import inspect
import sys


def install():
    if sys.platform != "win32":
        return
    from homeassistant.helpers import frame

    original = frame.get_integration_frame

    def get_integration_frame(exclude_integrations=None):
        try:
            return original(exclude_integrations)
        except frame.MissingIntegrationFrame:
            current = inspect.currentframe()
            while current is not None:
                filename = current.f_code.co_filename.replace("\\", "/")
                for prefix in ("custom_components/", "homeassistant/components/"):
                    index = filename.find(prefix)
                    if index < 0:
                        continue
                    domain = filename[index + len(prefix):].split("/", 1)[0]
                    if domain in (exclude_integrations or set()):
                        continue
                    return frame.IntegrationFrame(
                        custom_integration=prefix == "custom_components/",
                        integration=domain, module=current.f_globals.get("__name__"),
                        relative_filename=filename[index:], frame=current)
                current = current.f_back
            raise

    frame.get_integration_frame = get_integration_frame
