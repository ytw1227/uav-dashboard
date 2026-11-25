"""Passive navigation observations, shared by new captures and exact-replay backfills.

No research module imports, policy evaluations, RF calculations or random draws.
Every wrapper returns the exact object from its single native callback invocation.
"""


def progress_sample(nav, now):
    """Copy native state after update; a pending move has not physically started."""
    total = len(nav.survey.waypoints)
    record, move, pending = nav.record, nav.move, nav.pending
    entry_waiting = bool(nav.entry_waiting)
    descent_check = nav.prove is not None
    if move is not None:
        state = move.kind
    elif pending is not None:
        state = pending[1]
    elif descent_check:
        state = "descent_check"
    elif entry_waiting:
        state = "entry_wait"
    elif nav.finished_at is not None:
        state = "complete"
    elif nav.clearing:
        state = "clearing"
    elif record is not None and record.get("verified_s") is not None:
        state = "crossing"
    else:
        state = "search"
    window_index = next((i for i, item in enumerate(nav.windows_log) if item is record), None)
    target = (move.target_m if move is not None else pending[0] if pending is not None
              else nav.window.height_m if nav.window is not None else None)
    return dict(time_s=round(float(now), 6), stage=int(nav.stage), waypoints_reached=int(nav.stage),
                waypoints_total=total, coverage=float(nav.covered[nav.city.observable].sum() / nav.survey.observable),
                clearing=bool(nav.clearing), stalled_s=round(float(nav.stalled) * .1, 6),
                entry_waiting=entry_waiting, descent_check=descent_check,
                active_window_index=window_index, altitude_relay_id=f"uav-{nav.relay}" if nav.relay is not None else None,
                target_height_m=float(target) if target is not None else None,
                heights_m=[float(z) for z in nav.heights], state=state,
                motion_pending=pending is not None,
                move_start_s=float(move.start_s) if move is not None else None,
                move_end_s=float(move.end_s) if move is not None else None)


class OperationProbe:
    def __init__(self):
        self.progress = []
        self.checks = []
        self.navigators = 0
        self.nav = None
        self.last_update_s = None

    def attach(self, nav):
        if self.navigators:
            raise ValueError("한 비행의 운용 기록에 항법 인스턴스가 중복 연결되었습니다.")
        # Check the callback contract before wrapping anything. An incompatible new
        # controller must fail explicitly, never silently publish fabricated records.
        native_update = nav.update
        native_confirm, native_descent_safe = nav.confirm, nav.descent_safe
        self.navigators += 1
        self.nav = nav

        def confirm(now, since, relay):
            result = native_confirm(now, since, relay)
            self.checks.append(dict(time_s=float(now), kind="confirmation", since_s=float(since),
                                    relay=int(relay), drone_id=f"uav-{relay}", passed=bool(result),
                                    stage=int(nav.stage)))
            return result

        def descent_safe(now, relay, target):
            result = native_descent_safe(now, relay, target)
            self.checks.append(dict(time_s=float(now), kind="descent_safe", target_m=float(target),
                                    relay=int(relay), drone_id=f"uav-{relay}", passed=bool(result),
                                    stage=int(nav.stage)))
            return result

        def update(now):
            result = native_update(now)
            self.last_update_s = round(float(now), 6)
            if abs(now * 2 - round(now * 2)) < 1e-8:
                self.progress.append(progress_sample(nav, now))
            return result

        nav.update = update
        nav.confirm, nav.descent_safe = confirm, descent_safe
        return nav

    def recording(self, end_time_s=None):
        if self.navigators != 1 or not self.progress:
            raise ValueError("실제 항법 운용 기록을 확인할 수 없습니다.")
        if end_time_s is not None:
            end = round(float(end_time_s), 6)
            if abs(end * 2 - round(end * 2)) > 1e-8:
                raise ValueError("운용 기록의 종료 시각이 원 비행 시계와 다릅니다.")
            if self.progress[-1]["time_s"] != end:
                # At MAX_MISSION the loop ends after the 7199.9 update; unlike
                # normal completion it never calls update(7200). Read the final
                # native state without advancing navigation or evaluating RF.
                if (self.progress[-1]["time_s"] != end - .5
                        or self.last_update_s is None
                        or not end - .100001 <= self.last_update_s <= end):
                    raise ValueError("원 비행의 마지막 운용 상태와 종료 시각을 확인할 수 없습니다.")
                terminal = progress_sample(self.nav, end)
                terminal.update(terminal_snapshot=True, observed_after_update_s=self.last_update_s)
                self.progress.append(terminal)
        return dict(schema_version=1, progress=self.progress, checks=self.checks,
                    source="native_navigation_callbacks", source_update_s=.1, sample_s=.5)
