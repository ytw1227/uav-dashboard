"""Compact team radio rows, exposed as ordinary dashboard-contract JSON in SQLite."""
import json


FIELDS = ("beacon_rx_power_dbm", "beacon_sinr_db", "last_ack_received_power_dbm",
          "last_ack_sinr_db", "ack_success_ratio_in_interval")
_AGE_SLOTS = (6, 6, 7, 7, 8)


def _sql_members(expressions):
    entries = [
        f"""CASE WHEN field_mask & {1 << i} THEN '"{field}":' || ({expr}) || ',' ELSE '' END"""
        for i, (field, expr) in enumerate(zip(FIELDS, expressions))
    ]
    return "rtrim(" + " || ".join(entries) + ", ',')"


def observation_schema():
    # SQLite's -> returns the original JSON number token. Reading the token as
    # a REAL and reformatting it here can change the last significant digit.
    values = [f"scalars -> {i + 1}" for i in range(len(FIELDS))]
    for i in (2, 3):
        values[i] = f"CASE WHEN field_mask & {1 << (i+3)} THEN scalars -> {i-1} ELSE scalars -> {i+1} END"
    validity = [f"CASE WHEN ({expr}) = 'null' THEN 'false' ELSE 'true' END" for expr in values]
    ages = [f"scalars -> {slot}" for slot in _AGE_SLOTS]
    return f"""
    CREATE TABLE observations(
        time_s REAL NOT NULL, observer TEXT NOT NULL, peer TEXT NOT NULL,
        payload GENERATED ALWAYS AS (
            CASE WHEN original IS NOT NULL THEN original ELSE printf(
                '{{"observed_at_s":%s,"available_at_s":%s,"observer_id":%s,"peer_id":%s,"values":{{%s}},"valid":{{%s}},"ages":{{%s}}}}',
                scalars -> 0, scalars -> 0, json_quote(observer), json_quote(peer),
                {_sql_members(values)}, {_sql_members(validity)}, {_sql_members(ages)}) END
        ) VIRTUAL,
        field_mask INTEGER, scalars TEXT, original BLOB,
        PRIMARY KEY(observer,peer,time_s)
    ) WITHOUT ROWID;
    CREATE INDEX observations_time ON observations(time_s);
    """


def storage_row(t, observer, peer, row, pack):
    """Keep every scalar lexeme, including negative zero. Preserve unknown formats verbatim."""
    values, ages, valid = row["values"], row["ages"], row["valid"]
    fields = set(values)
    if (set(row) != {"observed_at_s", "available_at_s", "observer_id", "peer_id", "values", "ages", "valid"}
            or fields != set(ages) or fields != set(valid) or not fields.issubset(FIELDS)
            or row["observed_at_s"] != t or repr(row["available_at_s"]) != repr(row["observed_at_s"])
            or row["observer_id"] != observer or row["peer_id"] != peer
            or any(valid[key] is not (value is not None) for key, value in values.items())):
        return t, observer, peer, None, None, pack(row)
    mask = sum(1 << i for i, field in enumerate(FIELDS) if field in fields)
    scalars = [row["observed_at_s"], *(values.get(field) for field in FIELDS), None, None, None]
    stored_age_slots = set()
    for slot, field in zip(_AGE_SLOTS, FIELDS):
        if field in fields:
            if slot in stored_age_slots and repr(scalars[slot]) != repr(ages[field]):
                return t, observer, peer, None, None, pack(row)
            scalars[slot] = ages[field]
            stored_age_slots.add(slot)
    for i in (2, 3):
        if scalars[i+1] is not None and repr(scalars[i+1]) == repr(scalars[i-1]):
            mask |= 1 << (i+3)
            scalars[i+1] = None
    return t, observer, peer, mask, json.dumps(scalars, allow_nan=False, separators=(",", ":")), None
