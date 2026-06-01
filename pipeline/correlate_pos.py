import json
import csv
import glob
import uuid
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import requests

def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-dir", default="data/events")
    parser.add_argument("--pos-file", default="data/pos_transactions.csv")
    parser.add_argument("--api-url", default=None)
    args = parser.parse_args()

    # Load POS
    transactions = defaultdict(list)
    try:
        with open(args.pos_file, "r") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) < 10: continue
                date_str = row[6].strip()
                time_str = row[7].strip()
                try:
                    ts = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                
                store_id = row[9].strip()
                if store_id == "ST1008":
                    store_id = "STORE_BLR_002"
                
                transactions[store_id].append(ts)
    except FileNotFoundError:
        print(f"POS file {args.pos_file} not found. Skipping POS correlation.")
        return

    # Load events
    visitor_billing_sessions = defaultdict(lambda: defaultdict(list))
    
    events_files = glob.glob(f"{args.events_dir}/*.jsonl")
    for fpath in events_files:
        if "abandonments" in fpath: continue # Skip if already generated
        with open(fpath, "r") as f:
            for line in f:
                if not line.strip(): continue
                ev = json.loads(line)
                store = ev["store_id"]
                visitor = ev["visitor_id"]
                etype = ev["event_type"]
                ts = parse_iso(ev["timestamp"])

                if etype == "BILLING_QUEUE_JOIN":
                    visitor_billing_sessions[store][visitor].append({"join": ts, "exit": None, "event": ev})
                elif etype == "ZONE_EXIT" and ev.get("zone_id") == "BILLING_COUNTER":
                    sessions = visitor_billing_sessions[store][visitor]
                    if sessions and sessions[-1]["exit"] is None:
                        sessions[-1]["exit"] = ts
                elif etype == "EXIT":
                    sessions = visitor_billing_sessions[store][visitor]
                    if sessions and sessions[-1]["exit"] is None:
                        sessions[-1]["exit"] = ts

    abandonment_events = []
    
    for store_id, visitors in visitor_billing_sessions.items():
        store_txs = transactions.get(store_id, [])
        for visitor_id, sessions in visitors.items():
            for sess in sessions:
                exit_time = sess["exit"] or (sess["join"] + timedelta(minutes=5))
                join_time = sess["join"]
                
                converted = False
                for tx_ts in store_txs:
                    if join_time <= tx_ts <= exit_time + timedelta(minutes=5):
                        converted = True
                        break
                
                if not converted:
                    base_ev = sess["event"]
                    ab_ev = {
                        "event_id": str(uuid.uuid4()),
                        "store_id": base_ev["store_id"],
                        "camera_id": base_ev["camera_id"],
                        "visitor_id": base_ev["visitor_id"],
                        "event_type": "BILLING_QUEUE_ABANDON",
                        "timestamp": exit_time.isoformat(),
                        "zone_id": base_ev["zone_id"],
                        "dwell_ms": int((exit_time - join_time).total_seconds() * 1000),
                        "is_staff": base_ev["is_staff"],
                        "confidence": base_ev["confidence"],
                        "metadata": {
                            "queue_depth": base_ev["metadata"].get("queue_depth"),
                            "sku_zone": base_ev["metadata"].get("sku_zone"),
                            "session_seq": base_ev["metadata"]["session_seq"] + 1,
                        }
                    }
                    abandonment_events.append(ab_ev)

    print(f"Generated {len(abandonment_events)} BILLING_QUEUE_ABANDON events.")
    
    if abandonment_events:
        out_file = f"{args.events_dir}/abandonments.jsonl"
        with open(out_file, "w") as f:
            for ev in abandonment_events:
                f.write(json.dumps(ev) + "\n")
                
        if args.api_url:
            ingest_url = f"{args.api_url.rstrip('/')}/events/ingest"
            for i in range(0, len(abandonment_events), 500):
                batch = abandonment_events[i:i+500]
                try:
                    res = requests.post(ingest_url, json={"events": batch})
                    res.raise_for_status()
                except Exception as e:
                    print(f"Failed to ingest abandonments: {e}")

if __name__ == "__main__":
    main()
