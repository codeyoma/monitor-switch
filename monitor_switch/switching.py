"""One owner of switch admission, accepted-command history, and completion."""
import threading

from .usb import AutomaticRequests


class SwitchBusy(RuntimeError):
    """A request was rejected before a native write could begin."""


class SwitchBatch:
    def __init__(self, profiles, runner, notify, *, stopping=None):
        self.profiles, self.runner, self.notify = profiles, runner, notify
        self.stopping = stopping if stopping is not None else threading.Event()
        self._operation = threading.Lock()
        self.automatic_requests = AutomaticRequests()
        self.sent_inputs = {}

    def send(self, target, identities, automatic=False, *, eligible=None):
        if (target not in ('mac', 'windows') or type(automatic) is not bool
                or not isinstance(identities, list) or not identities
                or any(not isinstance(identity, str) for identity in identities)
                or len(set(identities)) != len(identities)
                or not set(identities) <= {p['id'] for p in self.profiles}):
            raise ValueError('Only configured monitors and the mac/windows destination are accepted.')
        requested = [p for p in self.profiles if p['id'] in identities]
        if self.stopping.is_set():
            return self._unavailable(requested)
        if not self._operation.acquire(blocking=False):
            raise SwitchBusy('A switch is already in progress. No command sent.')
        try:
            if self.stopping.is_set():
                return self._unavailable(requested)
            # Recheck a USB observation after admission, before touching history.
            if eligible is not None and not eligible():
                return {'results':[dict(id=p['id'], status='skipped', write_attempted=False,
                    message='USB observation is no longer current; no command sent.') for p in requested]}
            return self._execute(target, identities, requested, automatic)
        finally:
            self._operation.release()

    @staticmethod
    def _unavailable(profiles):
        return {'results':[dict(id=p['id'], status='unavailable', write_attempted=False,
                               error='Helper is stopping.') for p in profiles]}

    def stop(self, timeout=20):
        self.stopping.set()
        if not self._operation.acquire(timeout=timeout):
            raise RuntimeError('A worker is still stopping. Please wait before quitting.')
        self._operation.release()

    def _execute(self, target, identities, requested, automatic):
        if not self.automatic_requests.allow(target, identities, automatic):
            result = {'results':[dict(id=p['id'],status='skipped',write_attempted=False,
                                      error='Duplicate USB arrival; no command sent.') for p in requested]}
            self.notify('result', target=target, result=result)
            return result
        skipped, pending = {}, []
        for profile in requested:
            if automatic and self.sent_inputs.get(profile['id']) == (profile['mode'], profile[target]):
                skipped[profile['id']] = dict(id=profile['id'], label=profile['label'], code=profile[target],
                    status='skipped', write_attempted=False, physical_switch_confirmed=False,
                    message='Same input as the last accepted app command; no input command sent.')
            else:
                pending.append(profile)
        if not pending:
            result = {'results':[skipped[p['id']] for p in requested]}
            self.notify('result', target=target, result=result)
            return result
        # An attempted change can take effect even if the worker fails. Forget
        # the previous command before sending; only clean sent results replace it.
        for profile in pending:
            self.sent_inputs.pop(profile['id'], None)
        self.notify('busy', target=target)
        try:
            result = self.runner(pending, target)
        except Exception as exc:
            result = {'error':str(exc)}
        if not isinstance(result, dict):
            result = {'error':'Invalid native worker report.'}
        rows = result.get('results')
        valid = (isinstance(rows,list) and len(rows) == len(pending)
                 and all(isinstance(r,dict) and isinstance(r.get('id'),str)
                         and r.get('status') in ('sent','uncertain','unavailable','skipped') for r in rows)
                 and {r.get('id') for r in rows} == {p['id'] for p in pending})
        if not valid:
            rows = [dict(id=p['id'],status='uncertain',write_attempted=None,
                         error=result.get('error','Invalid or incomplete native worker report.')) for p in pending]
        by_id = {row['id']:row for row in rows}
        for profile in pending:
            row = by_id[profile['id']]
            if (row['status'] == 'sent' and row.get('write_attempted') is True and not row.get('error')
                    and not result.get('error') and not result.get('worker_warning')):
                self.sent_inputs[profile['id']] = (profile['mode'], profile[target])
        result['results'] = [skipped[p['id']] if p['id'] in skipped else by_id[p['id']] for p in requested]
        self.notify('result', target=target, result=result)
        return result

