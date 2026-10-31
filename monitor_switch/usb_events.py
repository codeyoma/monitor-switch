"""USB presence checks triggered by native notifications, never an idle timer."""
import threading
import time


class USBPresence:
    """Consume a snapshot after the notification burst has settled."""
    def __init__(self):
        self.stable = None

    def observe(self, count, error=None):
        result = {'count':count, 'trigger':False, 'event_driven':True}
        if error:
            return dict(result, state='error', message=error)
        if count not in (0,1):
            self.stable = None
            return dict(result, state='ambiguous', message='Multiple matching USB devices; automatic switching blocked.')
        previous, self.stable = self.stable, count
        return dict(result, state='present' if count else 'absent', baseline=previous is None,
                    trigger=previous == 0 and count == 1)


def native_source(changed, failed):
    import sys
    from .usb_native import MacUSBEvents, WindowsUSBEvents
    if sys.platform == 'darwin':
        return MacUSBEvents(changed, failed)
    if sys.platform == 'win32':
        return WindowsUSBEvents(changed, failed)
    raise RuntimeError('Native USB notifications require macOS or Windows. No polling fallback.')


class USBEventWatcher:
    def __init__(self, observed, *, source_factory=native_source, settle=1.0, enabled=True):
        self.observed, self.source_factory, self.settle = observed, source_factory, settle
        self.condition = threading.Condition()
        self.ready = threading.Event()
        self._enabled, self._stopped = enabled, False
        self._generation, self._sequence = 0, 0
        self._pending, self._deadline = enabled, 0.0
        self._fatal = None
        self.scan_count = 0

    def changed(self):
        with self.condition:
            if self._enabled and not self._stopped:
                self._sequence += 1
                self._pending = True
                self._deadline = time.monotonic() + self.settle
                self.condition.notify_all()

    def failed(self, message):
        with self.condition:
            self._fatal = message
            self.condition.notify_all()

    def set_enabled(self, enabled):
        with self.condition:
            self._enabled = bool(enabled)
            self._generation += 1
            self._sequence += 1
            self._pending = self._enabled
            self._deadline = 0.0
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self._stopped = True
            self.condition.notify_all()

    def current(self, result):
        with self.condition:
            return (self._enabled and not self._stopped and not self._fatal
                    and result.get('generation') == self._generation
                    and result.get('sequence') == self._sequence)

    def run(self):
        source, generation, presence = None, -1, USBPresence()
        try:
            with self.condition:
                if self._stopped:
                    return
            source = self.source_factory(self.changed, self.failed)
            source.start()  # Subscribe before the baseline to avoid a missing-event gap.
            while True:
                with self.condition:
                    while True:
                        if self._stopped:
                            return
                        if self._fatal:
                            raise RuntimeError(self._fatal)
                        if not self._enabled or not self._pending:
                            if not self._enabled:
                                self.ready.set()
                            self.condition.wait()  # No timeout and no periodic USB query.
                            continue
                        remaining = self._deadline - time.monotonic()
                        if remaining > 0:
                            self.condition.wait(remaining)  # One-shot event stabilization only.
                            continue
                        token = (self._generation, self._sequence)
                        self._pending = False
                        break
                count, error = None, None
                try:
                    self.scan_count += 1
                    count = source.count()
                except Exception as exc:
                    error = str(exc)
                with self.condition:
                    if self._stopped:
                        return
                    if token != (self._generation, self._sequence) or not self._enabled:
                        continue  # Discard a snapshot overtaken by an event or off/on.
                    if generation != self._generation:
                        generation, presence = self._generation, USBPresence()
                    result = presence.observe(count, error)
                    result.update(generation=token[0], sequence=token[1])
                self.observed(result)
                self.ready.set()
        except Exception as exc:
            self.observed({'state':'error', 'count':None, 'trigger':False, 'event_driven':True,
                           'message':str(exc) + ' · USB event monitoring stopped; no polling fallback.'})
        finally:
            with self.condition:
                self._stopped = True
            self.ready.set()
            if source is not None:
                try:
                    source.stop()
                except Exception as exc:
                    self.observed({'state':'error', 'count':None, 'trigger':False,
                                   'message':'USB notification cleanup failed: ' + str(exc)})


def check_idle(seconds=3):
    """Read-only packaged-build check; observations never switch any monitor."""
    if type(seconds) not in (int,float) or not 0 < seconds <= 10:
        raise ValueError('USB event check duration must be between 0 and 10 seconds.')
    results = []
    watcher = USBEventWatcher(results.append)
    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    try:
        if not watcher.ready.wait(6):
            raise RuntimeError('Native USB event observer did not become ready.')
        before = watcher.scan_count
        threading.Event().wait(seconds)  # Diagnostic observation window, not a query loop.
        after = watcher.scan_count
    finally:
        watcher.stop()
        thread.join(4)
        if thread.is_alive():
            raise RuntimeError('Native USB event observer did not shut down.')
    return {'read_only':True, 'event_driven':True, 'monitor_commands_sent':0,
            'baseline_count':results[0].get('count') if results else None,
            'idle_seconds':seconds, 'queries_before_idle':before, 'queries_after_idle':after,
            'errors':[row['message'] for row in results if row['state']=='error']}
