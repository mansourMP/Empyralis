import sqlite3
import json
import uuid
import datetime
import os
from pathlib import Path

STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME")
    or os.getenv("ORION_STATE_HOME")
    or (Path.home() / ".empyralis" / "state")
).expanduser()
DB_PATH = (
    os.getenv("EMPYRALIS_VIRAL_FACTORY_DB_PATH")
    or os.getenv("ORION_VIRAL_FACTORY_DB_PATH")
    or str(STATE_HOME / "viral_factory.db")
)

# The Gold Standard Wiring
WORKFLOW_PAYLOAD = {
    "nodes": [
        {
            "id": "trigger_1", "type": "trigger", "position": {"x": 100, "y": 50},
            "data": {"label": "Trend Alert", "type": "webhook"}
        },
        {
            "id": "prep_network", "type": "tool", "position": {"x": 100, "y": 200},
            "data": {
                "label": "1. Prep Network Check",
                "action": "execute_command",
                "command": """
python3 -c '
import json, uuid, os, hashlib
topic = "Mars Exploration" # In production, this comes from Trigger
exec_id = "run_" + uuid.uuid4().hex[:8]
run_dir = "/tmp/agency_os/" + exec_id
os.makedirs(run_dir, exist_ok=True)
payload = { "topic": topic, "execution_id": exec_id, "node_id": "network_prep" }
path = f"{run_dir}/payload_network.json"
# Shared LATEST file for simplified sequential demo
latest_path = "/tmp/agency_os/LATEST_PAYLOAD.json"
with open(path, "w") as f: json.dump(payload, f)
with open(latest_path, "w") as f: json.dump(payload, f)
print(json.dumps({"payload_path": path, "preview": payload}))
'
"""
            }
        },
        {
            "id": "exec_network", "type": "tool", "position": {"x": 100, "y": 400},
            "data": {
                "label": "2. Run: Network Check",
                "action": "execute_command",
                "command": """
./venv/bin/python python_engine/agency_logic.py astronomy check_network --in "/tmp/agency_os/LATEST_PAYLOAD.json"
"""
            }
        },
        {
            "id": "prep_critic", "type": "tool", "position": {"x": 100, "y": 600},
            "data": {
                "label": "3. Prep Critic Eval",
                "action": "execute_command",
                "command": """
python3 -c '
import json, uuid, os
# Simulate generating a draft
draft_text = "Mars is a planet. It is red. We should go there."
draft_path = "/tmp/agency_os/draft_v1.txt"
with open(draft_path, "w") as f: f.write(draft_text)

payload = { 
    "draft": draft_text, 
    "model": "gemini-1.5-flash",
    "node_id": "critic_prep"
}
latest_path = "/tmp/agency_os/LATEST_PAYLOAD.json"
with open(latest_path, "w") as f: json.dump(payload, f)
print(json.dumps({"draft_path": draft_path, "preview": "Draft created"}))
'
"""
            }
        },
        {
            "id": "exec_critic", "type": "tool", "position": {"x": 100, "y": 800},
            "data": {
                "label": "4. Run: Critic Eval",
                "action": "execute_command",
                "command": """
./venv/bin/python python_engine/agency_logic.py astronomy critic_eval --in "/tmp/agency_os/LATEST_PAYLOAD.json"
"""
            }
        },
        {
            "id": "prep_safety", "type": "tool", "position": {"x": 100, "y": 1000},
            "data": {
                "label": "5. Prep Safety Ticket",
                "action": "execute_command",
                "command": """
python3 -c '
import json
payload = { 
    "type": "PUBLISH_ACTION", 
    "data": { "platform": "Twitter", "content": "Mars is cool." }
}
latest_path = "/tmp/agency_os/LATEST_PAYLOAD.json"
with open(latest_path, "w") as f: json.dump(payload, f)
print(json.dumps({"ticket_type": "PUBLISH_ACTION", "status": "PREPPED"}))
'
"""
            }
        },
        {
            "id": "exec_safety", "type": "tool", "position": {"x": 100, "y": 1200},
            "data": {
                "label": "6. Create Safety Ticket",
                "action": "execute_command",
                "command": """
./venv/bin/python python_engine/agency_logic.py astronomy safety_ticket --in "/tmp/agency_os/LATEST_PAYLOAD.json"
"""
            }
        }
    ],
    "edges": [
        {"id": "e1", "source": "trigger_1", "target": "prep_network"},
        {"id": "e2", "source": "prep_network", "target": "exec_network"},
        {"id": "e3", "source": "exec_network", "target": "prep_critic"},
        {"id": "e4", "source": "prep_critic", "target": "exec_critic"},
        {"id": "e5", "source": "exec_critic", "target": "prep_safety"},
        {"id": "e6", "source": "prep_safety", "target": "exec_safety"}
    ]
}

def seed():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Get or Create User
        cursor.execute("SELECT id FROM users LIMIT 1")
        user = cursor.fetchone()
        if not user:
            user_id = str(uuid.uuid4())
            cursor.execute("INSERT INTO users (id, email, name, updatedAt) VALUES (?, ?, ?, ?)", 
                           (user_id, "admin@agency.os", "Admin", datetime.datetime.now()))
            print(f"👤 Created Admin User: {user_id}")
        else:
            user_id = user[0]

        # 2. Get or Create Organization
        cursor.execute("SELECT id FROM organizations LIMIT 1")
        org = cursor.fetchone()
        if not org:
            org_id = str(uuid.uuid4())
            cursor.execute("INSERT INTO organizations (id, name, slug, updatedAt) VALUES (?, ?, ?, ?)", 
                           (org_id, "Agency Inc", "agency-inc", datetime.datetime.now()))
            print(f"🏢 Created Organization: {org_id}")
        else:
            org_id = org[0]

        # 3. Get Target Workspace
        # Try to find 'default' workspace first (Standard Dev Environment)
        cursor.execute("SELECT id FROM workspaces WHERE id = 'default'")
        existing_default = cursor.fetchone()
        
        if existing_default:
            ws_id = "default"
            print(f"📂 Targeting Default Workspace: {ws_id}")
        else:
            # Fallback to org-based lookup
            cursor.execute("SELECT id FROM workspaces WHERE organizationId = ? LIMIT 1", (org_id,))
            ws = cursor.fetchone()
            if not ws:
                ws_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO workspaces (id, organizationId, name, updatedAt) VALUES (?, ?, ?, ?)", 
                               (ws_id, org_id, "Agency Workspace", datetime.datetime.now()))
                print(f"📂 Created Workspace: {ws_id}")
            else:
                ws_id = ws[0]
                print(f"📂 Using Existing Workspace: {ws_id}")

        # 4. Insert Workflow
        wf_id = str(uuid.uuid4())
        definition_json = json.dumps(WORKFLOW_PAYLOAD)
        now = datetime.datetime.utcnow().isoformat() + "Z"
        
        cursor.execute("""
            INSERT INTO workflows (id, workspaceId, name, description, definition, status, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (wf_id, ws_id, "Viral Factory (Gold Standard)", "Production-ready Agency OS workflow.", definition_json, "draft", now, now))
        
        conn.commit()
        print(f"✅ Workflow Seeded Successfully!")
        print(f"🆔 ID: {wf_id}")
        print(f"👉 Link: http://localhost:3000/workflows/{wf_id}")

    except Exception as e:
        print(f"❌ Database Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    seed()
