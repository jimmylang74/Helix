"""
ChannelManager — registry and lifecycle manager for all IM channels.

Central point to register, start, stop, and query channel adapters.
"""

from typing import Any, Dict, List, Optional

from imBots.base import ChannelAdapter
from modules.utils.logger import log_error, log_info


class ChannelManager:
    """Manages the lifecycle of all registered IM channel adapters."""

    def __init__(self):
        self._channels: Dict[str, ChannelAdapter] = {}

    def register(self, channel: ChannelAdapter) -> None:
        """Register a channel adapter by its channel_type."""
        ch_type = channel.channel_type
        self._channels[ch_type] = channel
        log_info(f"[ChannelManager] Registered channel: {ch_type}")

    def unregister(self, channel_type: str) -> bool:
        """Unregister (and stop) a channel."""
        ch = self._channels.pop(channel_type, None)
        if ch is None:
            return False
        if ch.is_running:
            ch.stop()
        log_info(f"[ChannelManager] Unregistered channel: {channel_type}")
        return True

    def get(self, channel_type: str) -> Optional[ChannelAdapter]:
        """Get a registered channel adapter."""
        return self._channels.get(channel_type)

    def start_all(self) -> None:
        """Start all registered channels."""
        for ch_type, ch in self._channels.items():
            try:
                ch.start()
                log_info(f"[ChannelManager] Started: {ch_type}")
            except Exception as e:
                log_error(f"[ChannelManager] Failed to start {ch_type}: {e}")

    def stop_all(self) -> None:
        """Stop all running channels."""
        for ch_type, ch in self._channels.items():
            if ch.is_running:
                try:
                    ch.stop()
                except Exception as e:
                    log_error(f"[ChannelManager] Failed to stop {ch_type}: {e}")

    def list_channels(self) -> List[Dict[str, Any]]:
        """Return status info for all registered channels."""
        result = []
        for ch_type, ch in self._channels.items():
            try:
                status = ch.get_status()
                result.append(status.to_dict())
            except Exception as e:
                result.append({
                    "channel_type": ch_type,
                    "is_running": False,
                    "is_authenticated": False,
                    "error": str(e),
                })
        return result
