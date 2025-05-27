from flask import Flask, jsonify, request
import random
import datetime
import uuid # For potentially more unique IDs if needed later

app = Flask(__name__)

# Helper function to create past timestamps
def past_datetime(days_ago=0, hours_ago=0, minutes_ago=0):
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago, hours=hours_ago, minutes=minutes_ago)

# Mock data for ServiceNow-like incidents - All closed with problem and solution
incidents_db = {
    "INC001001": {
        "number": "INC001001",
        "state": "Closed", # Changed
        "short_description": "Email server unresponsive",
        "assignment_group": "Network Support",
        "priority": "1 - Critical",
        "opened_at": past_datetime(days_ago=7, hours_ago=3).isoformat(),
        "caller_id": "abel.tuter@example.com",
        "category": "Hardware",
        "subcategory": "Server",
        "cmdb_ci": "email-server-01",
        "description": "Users reported being unable to send or receive emails starting around 9:00 AM. The main email server (email-server-01) was not responding to pings or connection attempts. Outlook clients were showing 'Disconnected'.",
        "resolution_code": "Solved (Workaround)", # Can be more specific
        "resolution_notes": "The primary email server's main network interface card (NIC) failed. Traffic was rerouted to the secondary NIC and a replacement primary NIC has been ordered. Full redundancy will be restored once the new NIC is installed and configured. Users confirmed email services were restored after failover.",
        "closed_at": past_datetime(days_ago=7, hours_ago=1).isoformat(), # Added
        "updated_on": past_datetime(days_ago=7, hours_ago=1).isoformat(),
        "updated_by": "system.auto_close",
        "resolved_by": "network.admin"
    },
    "INC001002": {
        "number": "INC001002",
        "state": "Resolved", # Changed (Resolved is often a precursor to Closed)
        "short_description": "Cannot access shared drive 'X:'",
        "assignment_group": "Desktop Support",
        "priority": "2 - High",
        "opened_at": past_datetime(days_ago=5, hours_ago=2).isoformat(),
        "caller_id": "beth.anglin@example.com",
        "category": "Software",
        "subcategory": "File Share",
        "cmdb_ci": "shared-drive-corp-fs03",
        "description": "User Beth Anglin reported she could not access the corporate shared drive mapped as 'X:'. Error message 'Network path not found'. Issue appeared after her morning login. Several colleagues in her department confirmed the same issue.",
        "resolution_code": "Solved (Permanently)",
        "resolution_notes": "The authentication token for the 'Finance' security group accessing 'shared-drive-corp-fs03' had expired on the file server. The token was renewed and SMB services for that share were restarted. Affected users were asked to log out and log back in to refresh their credentials. Access confirmed restored.",
        "closed_at": past_datetime(days_ago=5, minutes_ago=30).isoformat(), # Added
        "updated_on": past_datetime(days_ago=5, minutes_ago=30).isoformat(),
        "updated_by": "sarah.jones",
        "resolved_by": "sarah.jones"
    },
    "INC001003": {
        "number": "INC001003",
        "state": "Closed", # Changed
        "short_description": "VPN connection drops frequently",
        "assignment_group": "IT Security",
        "priority": "3 - Moderate",
        "opened_at": past_datetime(days_ago=3, hours_ago=6).isoformat(),
        "caller_id": "david.lee@example.com",
        "category": "Network",
        "subcategory": "VPN",
        "cmdb_ci": "vpn-concentrator-02",
        "description": "David Lee reported that his VPN connection (using Cisco AnyConnect) has been dropping every 15-20 minutes for the past two days, requiring him to re-authenticate each time. This impacts his ability to work on remote systems.",
        "resolution_code": "Solved (User Training)",
        "resolution_notes": "Investigation found that the user's home internet connection had high packet loss. Additionally, the user was unknowingly running a bandwidth-intensive P2P application in the background. Advised user to pause the application during work hours and to contact their ISP regarding connection stability. No issues found with the VPN concentrator or client software. User confirmed stability after pausing the application.",
        "closed_at": past_datetime(days_ago=3, hours_ago=1).isoformat(), # Added
        "updated_on": past_datetime(days_ago=3, hours_ago=1).isoformat(),
        "updated_by": "mark.chen",
        "resolved_by": "mark.chen"
    },
    "INC001004": {
        "number": "INC001004",
        "state": "Closed", # Was Resolved, changing to Closed for consistency
        "resolution_code": "Solved (No Fault Found)", # Example of a different resolution
        "resolution_notes": "User reported Application XYZ was not loading. Upon investigation, the application was found to be functioning normally for all other users and test accounts. The issue was isolated to the user's browser cache and cookies. Instructed the user to clear their browser cache and cookies, after which the application loaded successfully. No server-side changes were made.",
        "short_description": "Application XYZ not loading for a single user",
        "assignment_group": "Application Support",
        "priority": "4 - Low",
        "opened_at": past_datetime(days_ago=2, hours_ago=4).isoformat(),
        "caller_id": "emily.chen@example.com",
        "category": "Software",
        "subcategory": "Application",
        "cmdb_ci": "app-server-xyz-prod",
        "description": "Emily Chen reported that Application XYZ was failing to load this morning, showing a generic error page 'Error 500'. She tried restarting her browser with no success. She is the only one in her team facing this issue currently.",
        "closed_at": past_datetime(days_ago=2, hours_ago=2).isoformat(),
        "updated_on": past_datetime(days_ago=2, hours_ago=2).isoformat(),
        "updated_by": "ops.admin",
        "resolved_by": "ops.admin"
    },
    "INC001005": { # New closed ticket
        "number": "INC001005",
        "state": "Closed",
        "short_description": "Printer 'PRN-FIN-01' jamming frequently",
        "assignment_group": "Hardware Support",
        "priority": "3 - Moderate",
        "opened_at": past_datetime(days_ago=4, hours_ago=5).isoformat(),
        "caller_id": "george.bailey@example.com",
        "category": "Hardware",
        "subcategory": "Printer",
        "cmdb_ci": "PRN-FIN-01",
        "description": "The finance department printer, PRN-FIN-01, has been experiencing frequent paper jams today. Users have to clear jams every 20-30 pages. This is disrupting month-end report printing.",
        "resolution_code": "Solved (Hardware Replacement)",
        "resolution_notes": "Technician dispatched to inspect PRN-FIN-01. Found worn-out feed rollers and a misaligned paper tray component. Replaced the feed roller assembly and realigned the tray. Tested with 100 pages, no jams occurred. Advised users to ensure paper is loaded correctly and not overfilled.",
        "closed_at": past_datetime(days_ago=4, hours_ago=1).isoformat(),
        "updated_on": past_datetime(days_ago=4, hours_ago=1).isoformat(),
        "updated_by": "tech.support",
        "resolved_by": "tech.support"
    }
}

# Mock data for ServiceNow-like service requests (can remain as is, or be updated if needed)
requests_db = {
    "REQ002001": {
        "number": "REQ002001",
        "state": "Closed", # Updating to Closed for consistency if desired
        "stage": "Completed", # ServiceNow often uses 'stage' for requests
        "short_description": "Request for new software license",
        "assignment_group": "Software Asset Management",
        "priority": "3 - Moderate",
        "opened_at": past_datetime(days_ago=9, hours_ago=2).isoformat(),
        "requested_by": "abel.tuter@example.com",
        "requested_for": "abel.tuter@example.com",
        "item_details": {
            "name": "Adobe Photoshop License",
            "quantity": 1,
            "cost_center": "CC-MARKETING"
        },
        "description": "Need a new license for Adobe Photoshop for upcoming design project. Manager approval attached.",
        "approval": "Approved",
        "due_date": past_datetime(days_ago=7).isoformat(),
        "closed_at": past_datetime(days_ago=7).isoformat(),
        "updated_on": past_datetime(days_ago=7).isoformat(),
        "updated_by": "sam.specialist"
    },
    "REQ002002": {
        "number": "REQ002002",
        "state": "Closed", # Updating to Closed
        "stage": "Completed",
        "short_description": "New Laptop Request",
        "assignment_group": "Hardware Procurement",
        "priority": "2 - High",
        "opened_at": past_datetime(days_ago=15).isoformat(),
        "requested_by": "beth.anglin@example.com",
        "requested_for": "charlie.davis@example.com",
        "item_details": {
            "name": "Standard Developer Laptop Model X",
            "quantity": 1,
            "specifications": "16GB RAM, 512GB SSD, Core i7"
        },
        "description": "New laptop for new hire Charlie Davis, starting next Monday. All standard software to be pre-installed.",
        "approval": "Approved",
        "due_date": past_datetime(days_ago=8).isoformat(),
        "closed_at": past_datetime(days_ago=8).isoformat(),
        "updated_on": past_datetime(days_ago=8).isoformat(),
        "updated_by": "procurement.specialist"
    }
}

# Helper to generate next incident/request number
def get_next_number(prefix, db_keys):
    if not db_keys:
        # More robust starting number based on prefix
        if prefix == "INC":
            return "INC0010001" # ServiceNow typically has more digits
        elif prefix == "REQ":
            return "REQ0020001"
        else:
            return f"{prefix}{str(1).zfill(7)}"

    max_num = 0
    for key in db_keys:
        try:
            num_part_str = key.replace(prefix, "")
            if num_part_str.isdigit():
                num_part = int(num_part_str)
                if num_part > max_num:
                    max_num = num_part
        except ValueError:
            continue
    return f"{prefix}{str(max_num + 1).zfill(len(num_part_str) if max_num > 0 else 7)}"


@app.route('/')
def home():
    return "<h1>Mock ServiceNow API (Closed Tickets Focus)</h1><p>Endpoints: /api/v1/incidents, /api/v1/requests</p>"

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    return jsonify({"status": "UP"}), 200

# --- Incident Endpoints ---
@app.route('/api/v1/incidents', methods=['GET'])
def get_incidents():
    query_params = request.args
    limit = int(query_params.get('limit', 10))
    offset = int(query_params.get('offset', 0))

    # Start with all incidents
    filtered_incidents = list(incidents_db.values())

    # Apply filters
    if 'state' in query_params:
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('state', '').lower() == query_params['state'].lower()]
    if 'priority' in query_params:
        # Allow partial match for priority e.g., "1" for "1 - Critical"
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('priority', '').startswith(query_params['priority'])]
    if 'assignment_group' in query_params:
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('assignment_group', '').lower() == query_params['assignment_group'].lower()]
    if 'caller_id' in query_params:
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('caller_id', '').lower() == query_params['caller_id'].lower()]
    if 'cmdb_ci' in query_params:
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('cmdb_ci', '').lower() == query_params['cmdb_ci'].lower()]
    if 'category' in query_params:
        filtered_incidents = [inc for inc in filtered_incidents if inc.get('category', '').lower() == query_params['category'].lower()]


    # Sort by opened_at descending by default (newest first)
    sort_by = query_params.get('sort_by', 'opened_at')
    sort_order = query_params.get('sort_order', 'desc')

    reverse_sort = (sort_order.lower() == 'desc')

    # Ensure the sort_by key exists, default to 'opened_at' if not
    filtered_incidents.sort(key=lambda x: x.get(sort_by, x.get('opened_at')), reverse=reverse_sort)

    paginated_incidents = filtered_incidents[offset : offset + limit]

    return jsonify({
        "result": paginated_incidents,
        "total_records": len(filtered_incidents),
        "limit": limit,
        "offset": offset
    })

@app.route('/api/v1/incidents/<string:incident_number>', methods=['GET'])
def get_incident(incident_number):
    incident = incidents_db.get(incident_number.upper())
    if incident:
        return jsonify({"result": incident})
    else:
        return jsonify({"error": "Incident not found"}), 404

@app.route('/api/v1/incidents', methods=['POST'])
def create_incident():
    if not request.json or not 'short_description' in request.json or not 'caller_id' in request.json:
        return jsonify({"error": "Missing required fields: short_description, caller_id"}), 400
    if not 'description' in request.json: # Making description mandatory for new tickets
         return jsonify({"error": "Missing required field: description (problem details)"}), 400

    new_incident_number = get_next_number("INC", incidents_db.keys())
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # For this mock API, if a new ticket is created, we can decide if it should be open or closed by default.
    # Given the refactor focuses on closed tickets, let's make it possible to create a closed one directly
    # if resolution_notes are provided. Otherwise, it defaults to 'New'.
    is_closed_on_creation = 'resolution_notes' in request.json and request.json.get('state', '').lower() in ['closed', 'resolved']

    new_incident = {
        "number": new_incident_number,
        "state": request.json.get("state", "New"), # Default to New unless specified and resolution provided
        "short_description": request.json['short_description'],
        "assignment_group": request.json.get("assignment_group", "Service Desk"),
        "priority": request.json.get("priority", "4 - Low"),
        "opened_at": now_iso,
        "caller_id": request.json['caller_id'],
        "category": request.json.get("category", "Unknown"),
        "subcategory": request.json.get("subcategory", ""),
        "cmdb_ci": request.json.get("cmdb_ci", ""),
        "description": request.json['description'], # Problem description
        "updated_on": now_iso,
        "updated_by": "api_user_create" # Or derive from auth if implemented
    }

    if is_closed_on_creation:
        new_incident["state"] = request.json.get("state").capitalize() if request.json.get("state") else "Closed"
        new_incident["resolution_notes"] = request.json.get("resolution_notes", "Resolved via API creation.")
        new_incident["resolution_code"] = request.json.get("resolution_code", "Solved (Workaround)")
        new_incident["closed_at"] = now_iso
        new_incident["resolved_by"] = request.json.get("resolved_by", "api_user_resolve")
    else: # If creating an open ticket
        new_incident["state"] = request.json.get("state", "New")


    incidents_db[new_incident_number] = new_incident
    return jsonify({"result": new_incident}), 201

@app.route('/api/v1/incidents/<string:incident_number>', methods=['PUT', 'PATCH'])
def update_incident(incident_number):
    incident_number_upper = incident_number.upper()
    if incident_number_upper not in incidents_db:
        return jsonify({"error": "Incident not found"}), 404
    if not request.json:
        return jsonify({"error": "Request body cannot be empty for update"}), 400

    incident_to_update = incidents_db[incident_number_upper]
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for key, value in request.json.items():
        if key not in ["number", "opened_at", "caller_id"]: # Some fields might be immutable or handled specially
            incident_to_update[key] = value

    incident_to_update["updated_on"] = now_iso
    incident_to_update["updated_by"] = "api_user_update"

    # If state is changed to Resolved or Closed, and not already set, add resolution fields
    if incident_to_update.get("state", "").lower() in ["resolved", "closed"]:
        if "closed_at" not in incident_to_update or not incident_to_update["closed_at"]:
            incident_to_update["closed_at"] = now_iso
        if "resolution_notes" not in incident_to_update or not incident_to_update["resolution_notes"]:
            incident_to_update["resolution_notes"] = request.json.get("resolution_notes", "Ticket updated to closed/resolved state via API.")
        if "resolution_code" not in incident_to_update or not incident_to_update["resolution_code"]:
            incident_to_update["resolution_code"] = request.json.get("resolution_code", "Solved")
        if "resolved_by" not in incident_to_update or not incident_to_update["resolved_by"]:
            incident_to_update["resolved_by"] = request.json.get("resolved_by", "api_user_resolve")


    incidents_db[incident_number_upper] = incident_to_update
    return jsonify({"result": incident_to_update})

# --- Service Request Endpoints (Updated to also show closed state logic) ---
@app.route('/api/v1/requests', methods=['GET'])
def get_service_requests():
    query_params = request.args
    limit = int(query_params.get('limit', 10))
    offset = int(query_params.get('offset', 0))

    filtered_requests = list(requests_db.values())

    if 'state' in query_params:
        filtered_requests = [req for req in filtered_requests if req.get('state', '').lower() == query_params['state'].lower()]
    if 'requested_by' in query_params:
        filtered_requests = [req for req in filtered_requests if req.get('requested_by', '').lower() == query_params['requested_by'].lower()]
    if 'assignment_group' in query_params:
        filtered_requests = [req for req in filtered_requests if req.get('assignment_group', '').lower() == query_params['assignment_group'].lower()]

    # Sort by opened_at descending by default
    sort_by = query_params.get('sort_by', 'opened_at')
    sort_order = query_params.get('sort_order', 'desc')
    reverse_sort = (sort_order.lower() == 'desc')
    filtered_requests.sort(key=lambda x: x.get(sort_by, x.get('opened_at')), reverse=reverse_sort)

    paginated_requests = filtered_requests[offset : offset + limit]
    return jsonify({
        "result": paginated_requests,
        "total_records": len(filtered_requests),
        "limit": limit,
        "offset": offset
    })


@app.route('/api/v1/requests/<string:request_number>', methods=['GET'])
def get_service_request(request_number):
    s_request = requests_db.get(request_number.upper())
    if s_request:
        return jsonify({"result": s_request})
    else:
        return jsonify({"error": "Service Request not found"}), 404

@app.route('/api/v1/requests', methods=['POST'])
def create_service_request():
    if not request.json or not 'short_description' in request.json or not 'requested_for' in request.json:
        return jsonify({"error": "Missing required fields: short_description, requested_for"}), 400

    new_req_number = get_next_number("REQ", requests_db.keys())
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_req = {
        "number": new_req_number,
        "state": request.json.get("state", "Submitted"), # Default for new requests
        "stage": request.json.get("stage", "Requested"), # ServiceNow specific field
        "short_description": request.json['short_description'],
        "assignment_group": request.json.get("assignment_group", "Service Catalog Fulfillment"),
        "priority": request.json.get("priority", "4 - Low"),
        "opened_at": now_iso,
        "requested_by": request.json.get("requested_by", "api_user"),
        "requested_for": request.json['requested_for'],
        "item_details": request.json.get("item_details", {}),
        "description": request.json.get("description", ""),
        "approval": request.json.get("approval", "Not Yet Requested"),
        "updated_on": now_iso,
        "updated_by": "api_user_create"
    }

    # If creating a request that is immediately closed/fulfilled
    if new_req.get("state", "").lower() in ["closed", "fulfilled", "completed"]:
        new_req["closed_at"] = now_iso
        new_req["stage"] = request.json.get("stage", "Completed")


    requests_db[new_req_number] = new_req
    return jsonify({"result": new_req}), 201


if __name__ == '__main__':
    # For local development, you might want to enable reloader
    # For production, Gunicorn handles this.
    app.run(host='0.0.0.0', port=8080, debug=True)