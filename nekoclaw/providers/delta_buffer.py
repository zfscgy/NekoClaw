"""Buffer that merges stream deltas and releases them on a time interval."""

import time

from nekoclaw.providers.base import StreamDelta, ToolCallRequest


class DeltaBuffer:
    """Accumulates StreamDelta writes, merging same-type runs, and releases
    them via ``read()`` once ``interval`` seconds have elapsed since the first
    write, or immediately via ``clear()``."""

    __buffers: dict[str, "DeltaBuffer"] = {}

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._buffer: list[StreamDelta] = []
        self._first_time: float | None = None

    def get_buffer(self, key: str) -> "DeltaBuffer":
        if key not in self.__buffers:
            self.__buffers[key] = DeltaBuffer()
        return self.__buffers[key]

    def write(self, delta: StreamDelta) -> None:
        if self._first_time is None:
            self._first_time = time.monotonic()

        if delta.type in ("thinking", "content"):
            if self._buffer and self._buffer[-1].type == delta.type:
                prev = self._buffer[-1]
                self._buffer[-1] = StreamDelta(type=prev.type, content=prev.content + delta.content)
            else:
                self._buffer.append(delta)

        elif delta.type == "tool_call":
            tc = delta.content
            if not isinstance(tc, ToolCallRequest):
                self._buffer.append(delta)
                return
            last_msg = self._buffer[-1] if self._buffer else None
            if last_msg is not None and last_msg.type == "tool_call":
                prev = last_msg.content
                if (isinstance(prev, ToolCallRequest) and
                        prev.index == tc.index and tc.id and prev.id == tc.id):
                    self._buffer[-1] = delta
                    return
            self._buffer.append(delta)

        else:
            self._buffer.append(delta)

    def read(self) -> list[StreamDelta]:
        """Return merged deltas if the interval has elapsed; otherwise return empty."""
        if self._first_time is not None and time.monotonic() - self._first_time >= self.interval:
            return self._flush()
        return []

    def clear(self) -> list[StreamDelta]:
        """Return merged deltas and clear the buffer unconditionally."""
        return self._flush()

    def _flush(self) -> list[StreamDelta]:
        result, self._buffer, self._first_time = self._buffer, [], None
        return result

