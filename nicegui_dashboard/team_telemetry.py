"""Observation-only probes and display data shared by both native flight arms."""
from collections import deque
from contextlib import ExitStack, contextmanager
from unittest.mock import patch


PAIR_HEADS = ("next_onset_lt5s", "next_onset_3to4s", "next_onset_3to7s", "next_onset_lt7s")
WINDOWS = ((0, 5), (3, 4), (3, 7), (0, 7))


def legacy_capture_view(row, arrays, with_forecast):
    keys = ("positions","final_positions","offset_xy_m","plan_frames","scores","support_counts",
            "links","network","margin_db","applied_offsets","packet_counters","beacon_rx")
    projected = {key:arrays[key] for key in keys}
    if with_forecast:
        projected["fleet_onset_probability"] = arrays["fleet_onset_probability"]
    return {**row,"team_capture_version":"2" if with_forecast else "1"},projected


class NativeChannels:
    def __init__(self):
        self.loss, self.extra, self.shadow, self.noise = [], [], [], []
        self.sinr, self.pairs, self.shared = [], [], []
        self.ack_intervals = []
        self._attempts = self._successes = self._stamp = None

    @contextmanager
    def observe(self, runner, model):
        import numpy as np
        from adaptive_joint_expanded.targets import probabilities, TARGET_NAMES

        if tuple(TARGET_NAMES) != PAIR_HEADS:
            raise ValueError("팀 드론 쌍별 예측 대상 정의가 달라졌습니다.")
        geometry, radio, forecast = runner.geometry, runner.hop_probabilities, model.forecast

        def observed_geometry(*args, **kwargs):
            result = geometry(*args, **kwargs)
            self.extra.append(result[1][0].copy())
            return result

        def observed_radio(loss, interference, shadow, received_out, sinr_out):
            result = radio(loss, interference, shadow, received_out, sinr_out)
            self.loss.append(loss.copy())
            self.noise.append(interference.copy())
            self.shadow.append(shadow.copy())
            self.sinr.append(sinr_out.copy())
            return result

        def observed_forecast(values, masks, intents, geometry, support, route, native):
            result = forecast(values, masks, intents, geometry, support, route, native)
            timing = result[0].detach()
            if timing.shape[:-1] != support.shape or timing.shape[-1] != 17:
                raise ValueError("팀 쌍별 예측 출력의 관측 마스크나 분류가 달라졌습니다.")
            prediction = probabilities(timing)[0].cpu().numpy()
            supported = support[0].cpu().numpy()
            observed = prediction[supported]
            if (not np.isfinite(observed).all() or (observed < 0).any()
                    or (observed > 1 + 2**-22).any()):
                raise ValueError("지원되는 드론 쌍의 예측값이 확률 범위를 벗어났습니다.")
            self.pairs.append(np.where(supported[..., None], prediction.clip(0, 1), -1).astype('<f4'))
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "geometry", observed_geometry))
            stack.enter_context(patch.object(runner, "hop_probabilities", observed_radio))
            stack.enter_context(patch.object(model, "forecast", observed_forecast))
            yield

    def packets(self, packet, frame):
        import numpy as np
        if self._attempts is None:
            self._attempts = np.zeros_like(packet.attempts)
            self._successes = np.zeros_like(packet.successes)
            self._stamp = np.full_like(packet.last_ack, -1.)
        self._attempts += packet.attempts
        self._successes += packet.successes
        np.maximum(self._stamp, packet.last_ack, out=self._stamp)
        if frame % 5 == 0:
            self.shared.append(int(np.all(packet.known, axis=0).sum()))
            self.ack_intervals.append((self._attempts, self._successes, self._stamp))
            self._attempts = None

    def arrays(self, frames, ns, n):
        import numpy as np
        count = len(frames["links"])
        if any(len(rows) != count for rows in (self.loss, self.extra, self.shadow, self.noise,
                                                self.sinr, self.shared, self.ack_intervals)):
            raise ValueError("실제 전파·패킷 관측 시각과 비행 기록이 일치하지 않습니다.")
        if len(self.pairs) != len(frames["plan_frames"]):
            raise ValueError("드론 쌍별 예측과 정책 판단 시각이 일치하지 않습니다.")
        sinr = np.asarray(self.sinr)
        attempt, success, stamp = map(np.asarray, zip(*self.ack_intervals))
        return dict(radio_loss=np.asarray(self.loss), radio_extra=np.asarray(self.extra),
                    radio_shadow=np.asarray(self.shadow), radio_interference=np.asarray(self.noise),
                    radio_sinr=sinr, ack_attempts=attempt, ack_successes=success, ack_stamp=stamp,
                    shared_counts=np.asarray(self.shared),
                    pair_probabilities=np.asarray(self.pairs, dtype='<f4').reshape(-1,n-ns,n,4),
                    **margin_witnesses(sinr, frames["links"], ns))


def margin_witnesses(sinr, graph, ns):
    """Apply the original DATA+ACK 90% bidirectional display definition."""
    import numpy as np
    from scipy.special import erfc

    def success(db, bits):
        ber = .5 * erfc(np.sqrt(10. ** (np.clip(db, -100., 100.) / 10.)))
        return np.exp(bits * np.log1p(-ber))

    required = np.transpose(np.triu_indices(ns, 1))
    margins, pairs, paths, edges = [], [], [], []
    ids = np.triu_indices(graph.shape[1], 1)
    for batch in range(0, len(sinr), 128):
        signals = sinr[batch:batch+128]
        reverse = signals.swapaxes(1, 2)
        diff = np.abs(signals - reverse)
        lower, upper = np.full_like(diff, -20.), np.full_like(diff, 30.)
        for _ in range(48):
            mid = (lower + upper) * .5
            good = success(mid, 4608) * success(mid + diff, 112) >= .9
            lower, upper = np.where(good,lower,mid), np.where(good,mid,upper)
        weights = np.minimum(signals, reverse) - upper
        if not np.array_equal(weights[:,ids[0],ids[1]] >= 0, graph[batch:batch+len(weights),ids[0],ids[1]]):
            raise ValueError("90% 양방향 통신 여유의 부호와 실제 링크가 다릅니다.")
        widest = weights.copy()
        for k in range(weights.shape[-1]):
            widest = np.maximum(widest, np.minimum(widest[:,:,k,None],widest[:,None,k,:]))
        candidates = widest[:,required[:,0],required[:,1]]
        for matrix, alternatives in zip(weights, candidates):
            which = int(alternatives.argmin())
            score = float(alternatives[which])
            a,b = map(int,required[which])
            parents, queue = {a:None}, deque([a])
            while b not in parents and queue:
                node = queue.popleft()
                for neighbor in np.flatnonzero(matrix[node] >= score):
                    if neighbor not in parents:
                        parents[neighbor] = node
                        queue.append(neighbor)
            path = [b]
            while path[-1] != a:
                path.append(parents[path[-1]])
            path.reverse()
            bottleneck = min(sorted((i,j)) for i,j in zip(path,path[1:]) if matrix[i,j] == score)
            margins.append(score)
            pairs.append((a,b))
            paths.append(path + [-1]*(len(matrix)-len(path)))
            edges.append(bottleneck)
    return dict(display_margin=np.asarray(margins), margin_pair=np.asarray(pairs,dtype=np.int16),
                margin_path=np.asarray(paths,dtype=np.int16), margin_edge=np.asarray(edges,dtype=np.int16))


def add_manifest_fields(manifest, arrays):
    manifest["team_telemetry"] = dict(exact_margin="display_margin" in arrays, shared_series="shared_counts" in arrays)
    if "pair_probabilities" in arrays:
        manifest["head_definitions"].update({head:dict(scope="next_persistent_direct_link_outage",
            window_s=list(window), window_bounds="[)", minimum_outage_s=1, source="adaptive_joint_expanded.targets")
            for head,window in zip(PAIR_HEADS,WINDOWS)})
    if "ack_stamp" in arrays:
        for key,unit in [("last_ack_sinr_db","dB"),("last_ack_received_power_dbm","dBm"),
                         ("ack_success_ratio_in_interval","ratio")]:
            manifest["observation_definitions"][key] = dict(unit=unit, source="native packet slots, successful reverse ACK and per-RF-interval counts")


def radio_rows(arrays, ids):
    """Merge independently timed beacon, ACK and interval measurements by receiver.

    A slot's ACK travels from the DATA receiver back to the DATA sender. Its
    channel is sampled in that direction. Ratios are available only after
    the complete 0.5 s interval, including intervals with zero successes.
    """
    from collections import defaultdict
    import numpy as np
    if "ack_stamp" not in arrays:
        # Older replay backfills compare serialized rows with the original sealed database.
        for t,sender,receiver,power,sinr in arrays["beacon_rx"]:
            observer,peer = ids[int(receiver)],ids[int(sender)]
            values = dict(beacon_sinr_db=float(sinr),beacon_rx_power_dbm=float(power))
            yield float(t),observer,peer,dict(observed_at_s=t,available_at_s=t,observer_id=observer,peer_id=peer,
                values=values,valid=dict.fromkeys(values,True),ages=dict.fromkeys(values,0.0))
        return
    samples = defaultdict(list)
    for t, sender, receiver, power, sinr in arrays["beacon_rx"]:
        samples[int(receiver),int(sender)].append((float(t),float(power),float(sinr)))
    has_ack = "ack_stamp" in arrays
    for receiver, observer in enumerate(ids):
        for sender, peer in enumerate(ids):
            if sender == receiver:
                continue
            changes = {}
            for t,power,sinr in samples[receiver,sender]:
                changes[t] = {"beacon_rx_power_dbm": (power,t), "beacon_sinr_db": (sinr,t)}
            if has_ack:
                attempts = arrays["ack_attempts"][:,receiver,sender]
                successes = arrays["ack_successes"][:,receiver,sender]
                active = attempts > 0
                changed = active.copy()
                changed[1:] |= active[:-1]
                for k in np.flatnonzero(changed):
                    end = (int(k)+1)*.5
                    ratio = float(successes[k]/attempts[k]) if active[k] else None
                    changes.setdefault(end,{})["ack_success_ratio_in_interval"] = (ratio,end)
                    if successes[k]:
                        stamp = float(arrays["ack_stamp"][k,receiver,sender])
                        if not k*.5-1e-8 <= stamp <= end+1e-8:
                            raise ValueError("수신한 ACK 시각과 패킷 구간이 다릅니다.")
                        stamp = min(stamp,end)
                        change = changes.setdefault(stamp,{})
                        power = 10. - arrays["radio_loss"][k,sender,receiver] - arrays["radio_shadow"][k,sender,receiver]
                        change["last_ack_received_power_dbm"] = (float(power),stamp)
                        change["last_ack_sinr_db"] = (float(arrays["radio_sinr"][k,sender,receiver]),stamp)
            latest = {}
            for t,changed in sorted(changes.items()):
                latest.update(changed)
                values = {key:value for key,(value,_) in latest.items()}
                yield t,observer,peer,dict(observed_at_s=t,available_at_s=t,observer_id=observer,peer_id=peer,
                    values=values,valid={key:v is not None for key,v in values.items()},
                    ages={key:t-stamp for key,(_,stamp) in latest.items()})
