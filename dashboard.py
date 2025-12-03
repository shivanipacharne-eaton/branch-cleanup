import httpx
import streamlit as st
import matplotlib.pyplot as plt
from datetime import datetime, timezone
import threading
import time
import pandas as pd
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# ✅ Enable wide layout
st.set_page_config(layout="wide")

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Initialize session state
if "fetching" not in st.session_state:
    st.session_state.fetching = False
if "branches_to_delete" not in st.session_state:
    st.session_state.branches_to_delete = set()
if "deleted_branches" not in st.session_state:
    st.session_state.deleted_branches = set()
if "branch_details" not in st.session_state:
    st.session_state.branch_details = []
if "branch_categories" not in st.session_state:
    st.session_state.branch_categories = {"stale": [], "open_pr": [], "closed_pr": [], "no_pr": []}
if "delete_stale" not in st.session_state:
    st.session_state.delete_stale = False
if "delete_open_pr" not in st.session_state:
    st.session_state.delete_open_pr = False
if "delete_closed_pr" not in st.session_state:
    st.session_state.delete_closed_pr = False
if "delete_no_pr" not in st.session_state:
    st.session_state.delete_no_pr = False
if "deletion_complete" not in st.session_state:
    st.session_state.deletion_complete = False
if "refresh_graph" not in st.session_state:
    st.session_state.refresh_graph = False

# Shared data - use session state to persist across reruns
branch_details = st.session_state.branch_details
branch_categories = st.session_state.branch_categories
lock = threading.Lock()
total_pages_estimate = 50

fetching_flag = threading.Event()
fetch_completed = threading.Event()

# =============================
# Helper Functions
# =============================
def extract_github_username(email):
    """Extract username from GitHub noreply email format
    Example: 110465400+shivanipacharne-eaton@users.noreply.github.com -> shivanipacharne-eaton
    """
    if '@users.noreply.github.com' in email:
        # Extract the part before @
        local_part = email.split('@')[0]
        # If it has a +, get the part after it
        if '+' in local_part:
            return local_part.split('+')[1]
        # Otherwise return the whole local part
        return local_part
    # For regular emails, return as is
    return email

# =============================
# Delete Branches Function
# =============================
def delete_branches(token, owner, repo, branches_list):
    """Delete branches via GitHub API"""
    logging.info(f"🗑️ Starting deletion of {len(branches_list)} branches: {branches_list}")
    print(f"\n{'='*60}")
    print(f"DELETING {len(branches_list)} BRANCHES")
    print(f"{'='*60}")
    
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    client = httpx.Client(verify=False, timeout=60.0)
    
    deleted_count = 0
    failed_count = 0
    
    for branch_name in branches_list:
        delete_url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch_name}"
        try:
            response = client.delete(delete_url, headers=headers)
            if response.status_code == 204:
                deleted_count += 1
                logging.info(f"✅ Deleted ({deleted_count}/{len(branches_list)}): {branch_name}")
                print(f"✅ Deleted ({deleted_count}/{len(branches_list)}): {branch_name}")
                st.session_state.deleted_branches.add(branch_name)
                with lock:
                    # Remove from categories
                    for cat in branch_categories.values():
                        cat[:] = [(a, e, n) for a, e, n in cat if n != branch_name]
                    # Remove from branch_details
                    branch_details[:] = [b for b in branch_details if b["Branch"] != branch_name]
            else:
                failed_count += 1
                logging.error(f"❌ Failed to delete {branch_name}: {response.status_code}")
                print(f"❌ Failed to delete {branch_name}: Status {response.status_code}")
        except Exception as e:
            failed_count += 1
            logging.error(f"❌ Error deleting {branch_name}: {e}")
            print(f"❌ Error deleting {branch_name}: {e}")
    
    client.close()
    
    print(f"\n{'='*60}")
    print(f"DELETION SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Successfully deleted: {deleted_count}")
    print(f"❌ Failed: {failed_count}")
    print(f"Total processed: {len(branches_list)}")
    print(f"{'='*60}\n")
    logging.info(f"Deletion complete: {deleted_count} deleted, {failed_count} failed")
    status_placeholder.success(f"✅ Deleted {deleted_count} branches, {failed_count} failed")
    
    return deleted_count, failed_count

# =============================
# Notification Functions
# =============================
def generate_notification_summary(branches_by_author):
    """Generate a summary text for notifications"""
    summary = "🔔 GitHub Stale Branch Notification\n\n"
    summary += "The following branches have been identified as stale and may need attention:\n\n"
    
    for author_email, branches in sorted(branches_by_author.items()):
        author_name = branches[0]['author_name']
        summary += f"📧 {author_name} ({author_email}):\n"
        for branch in branches:
            summary += f"  - {branch['branch_name']} (last commit: {branch['last_commit']})\n"
        summary += "\n"
    
    return summary

def send_email_notification(smtp_server, smtp_port, sender_email, sender_password, recipient_email, subject, body):
    """Send email notification"""
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)

def prepare_notification_data(branch_list):
    """Group branches by author email"""
    branches_by_author = defaultdict(list)
    
    for branch in branch_list:
        branches_by_author[branch['Author Email']].append({
            'author_name': branch['Author'],
            'branch_name': branch['Branch'],
            'last_commit': branch['Last Commit'],
            'category': branch['Category']
        })
    
    return branches_by_author

def create_github_issue_notification(token, owner, repo, branches_by_author, issue_title="Stale Branch Cleanup Notification"):
    """Create a GitHub issue mentioning branch authors"""
    try:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        client = httpx.Client(verify=False, timeout=60.0)
        
        # Collect all unique author names as assignees
        assignees = set()
        for author_email, branches in branches_by_author.items():
            author_name = branches[0]['author_name']
            # Check if any branch is open_pr category
            has_open_pr = any(branch['category'] == 'open_pr' for branch in branches)
            
            if has_open_pr:
                # Extract username from GitHub noreply email for open PR branches
                github_username = extract_github_username(author_email)
                assignees.add(github_username)
                logging.info(f"Adding assignee: {github_username} (from {author_email}) - has open PR")
            else:
                assignees.add(author_name)
                logging.info(f"Adding assignee: {author_name} ({author_email})")
        
        # Convert set to list for API
        assignees = list(assignees)
        
        # Build issue body
        body = "## 🔔 Stale Branch Notification\n\n"
        body += "The following branches have been identified as stale and may need cleanup:\n\n"
        
        # Group branches by username (extracted from email for open_pr)
        branches_by_username = {}
        for author_email, branches in branches_by_author.items():
            author_name = branches[0]['author_name']
            has_open_pr = any(branch['category'] == 'open_pr' for branch in branches)
            
            if has_open_pr:
                username = extract_github_username(author_email)
            else:
                username = author_name
            
            # Consolidate branches under the same username
            if username not in branches_by_username:
                branches_by_username[username] = {
                    'emails': set(),
                    'branches': []
                }
            branches_by_username[username]['emails'].add(author_email)
            branches_by_username[username]['branches'].extend(branches)
        
        # Generate issue body with consolidated tables
        for username, data in sorted(branches_by_username.items()):
            emails_str = ', '.join(sorted(data['emails']))
            body += f"### @{username} ({emails_str})\n\n"
            logging.info(f"Adding section for @{username} with {len(data['branches'])} branches")
            
            body += "| Branch | Last Commit | Category |\n"
            body += "|--------|-------------|----------|\n"
            for branch in data['branches']:
                body += f"| `{branch['branch_name']}` | {branch['last_commit']} | {branch['category']} |\n"
            body += "\n"
        
        body += "---\n"
        body += "_Please review these branches and delete them if they are no longer needed._\n"
        
        # Create the issue with assignees
        issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        issue_data = {
            "title": issue_title,
            "body": body,
            "labels": ["branch-cleanup", "stale-branches"],
            "assignees": assignees
        }
        
        logging.info(f"Creating issue with {len(assignees)} assignees: {assignees}")
        
        response = client.post(issue_url, headers=headers, json=issue_data)
        client.close()
        
        if response.status_code == 201:
            issue_number = response.json()['number']
            issue_html_url = response.json()['html_url']
            return True, f"Issue #{issue_number} created successfully with {len(assignees)} assignees", issue_html_url
        else:
            return False, f"Failed to create issue: {response.status_code} - {response.text}", None
            
    except Exception as e:
        return False, f"Error creating issue: {str(e)}", None

# =============================
# Background Fetch Function (NO Streamlit calls)
# =============================
def fetch_branches_continuously(token, owner, repo, stale_days):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches"
    prs_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=all&per_page=100"

    # Create httpx client with SSL verification disabled
    client = httpx.Client(verify=False, timeout=60.0)
    
    # Fetch all PRs first
    pr_map = {}
    try:
        prs_response = client.get(prs_url, headers=headers)
        if prs_response.status_code == 200:
            prs = prs_response.json()
            pr_map = {pr['head']['ref']: 'open_pr' if pr['state'] == 'open' else 'closed_pr' for pr in prs}
            logging.info(f"Successfully fetched {len(prs)} PRs")
        elif prs_response.status_code == 403:
            error_msg = prs_response.json().get('message', 'Access forbidden')
            logging.error(f"403 Forbidden: {error_msg}")
            logging.error("Possible reasons: 1) Token needs 'repo' scope, 2) SSO authorization required (check https://github.com/settings/tokens), 3) Rate limit")
            client.close()
            return
        else:
            logging.error(f"Failed to fetch PRs: Status {prs_response.status_code} - {prs_response.text}")
            client.close()
            return
    except Exception as e:
        logging.error(f"Failed to fetch PRs: {e}")
        client.close()
        return

    page = 1
    while fetching_flag.is_set():
        url = f"{branches_url}?per_page=5&page={page}"
        logging.info(f"Fetching page {page} from GitHub API...")
        
        try:
            response = client.get(url, headers=headers)
            if response.status_code == 403:
                error_msg = response.json().get('message', 'Access forbidden')
                logging.error(f"403 Forbidden on page {page}: {error_msg}")
                break
            elif response.status_code != 200:
                logging.error(f"Failed with status {response.status_code}: {response.text}")
                break
        except Exception as e:
            logging.error(f"Failed to fetch branches page {page}: {e}")
            break

        data = response.json()
        if not data or isinstance(data, dict):
            logging.info("No more pages to fetch. Pagination complete.")
            break

        now = datetime.now(timezone.utc)

        with lock:
            for branch in data:
                name = branch['name']
                commit_url = branch['commit']['url']
                try:
                    commit_data = client.get(commit_url, headers=headers).json()
                    commit_date_str = commit_data['commit']['author']['date']
                except Exception as e:
                    logging.error(f"Failed to fetch commit for branch {name}: {e}")
                    continue
                commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                author_name = commit_data['commit']['author'].get('name', 'Unknown')
                author_email = commit_data['commit']['author'].get('email', 'Unknown')

                category = "stale" if (now - commit_date).days > stale_days else pr_map.get(name, "no_pr")
                branch_categories[category].append((author_name, author_email, name))
                branch_details.append({
                    "Branch": name,
                    "Last Commit": commit_date.strftime("%Y-%m-%d"),
                    "Category": category,
                    "Author": author_name,
                    "Author Email": author_email
                })

        page += 1
        time.sleep(0.5)

    client.close()
    fetching_flag.clear()  # ✅ Stop flag when done
    fetch_completed.set()  # Signal that fetching is complete

# =============================
# UI Layout
# =============================
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 1rem;
        padding: 1rem;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    div[data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 12px;
        color: white;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    section[data-testid="stSidebar"] {
        background-color: #f8f9fa;
    }
    .element-container {
        margin-bottom: 1rem;
    }
    div[data-testid="stCheckbox"] {
        margin-bottom: -1.5rem !important;
        padding-bottom: 0 !important;
    }
    div[data-testid="stCheckbox"] > label {
        margin-bottom: 0 !important;
        padding-bottom: 0 !important;
    }
    div[data-testid="stCheckbox"] > label > div {
        padding-bottom: 0 !important;
        margin-bottom: 0 !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 class="main-header"> GitHub Branch Management Dashboard</h1>', unsafe_allow_html=True)

# =============================
# Top Configuration Section
# =============================
st.markdown("### ⚙️ Configuration")

# First row: 4 input fields
config_col1, config_col2, config_col3, config_col4 = st.columns(4)

with config_col1:
    github_token = st.text_input("🔑 GitHub Token", type="password", value="")

with config_col2:
    owner = st.text_input("👤 Repository Owner", value="shivanipacharne-eaton")

with config_col3:
    repo = st.text_input("📦 Repository Name", value="kafka")

with config_col4:
    stale_days = st.number_input("⏰ Stale Branch Threshold (days)", min_value=1, value=90, help="Branches with no commits for this many days will be marked as stale")

# Second row: Start/Stop buttons side by side
btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([4, 1.5, 1.5, 4])

with btn_col2:
    if not st.session_state.fetching:
        start_btn = st.button("▶️ Start Fetching", type="primary")
    else:
        start_btn = None

with btn_col3:
    stop_btn = st.button("⏹️ Stop Fetching")

# Status row below buttons
status_placeholder = st.empty()

st.markdown("---")

# Main layout with 3 columns
col_left, col_center, col_right = st.columns([2, 3, 2], gap="large")

# ✅ Left Column: Branch Details Table
with col_left:
    st.markdown("### 📋 Branch Details")
    table_placeholder = st.empty()

# ✅ Center Column: Graph
with col_center:
    st.markdown("### 📊 Branch Analytics")
    graph_placeholder = st.empty()

# ✅ Right Column: Delete Options & Notifications
with col_right:
    st.markdown("### 🎯 Actions")
    
    # Common selection section placeholder
    selection_placeholder = st.empty()
    
    st.markdown("---")
    
    # Delete and Notification buttons side by side
    btn_col1, btn_col2 = st.columns(2)
    
    with btn_col1:
        st.markdown("#### 🗑️ Delete")
        delete_btn = st.button(
            "🗑️ Delete Selected", 
            type="primary", 
            use_container_width=True,
            key="delete_branches_btn"
        )
    
    with btn_col2:
        st.markdown("#### 📧 Notify")
        notify_btn = st.button(
            "📧 Notify Authors",
            use_container_width=True,
            key="notify_authors_btn",
            help="Send notifications to authors about their stale branches",
            disabled=st.session_state.fetching or len(branch_details) == 0
        )
    
    # Notification result placeholder
    notification_result_placeholder = st.empty()

# =============================
# Button Actions
# =============================
if start_btn and github_token:
    st.session_state.fetching = True
    fetching_flag.set()
    fetch_completed.clear()  # Reset completion flag
    status_placeholder.info("Fetching branches in background...")
    threading.Thread(target=fetch_branches_continuously, args=(github_token, owner, repo, stale_days), daemon=True).start()

if stop_btn and st.session_state.fetching:
    fetching_flag.clear()
    st.session_state.fetching = False
    status_placeholder.empty()
    st.warning("Fetching stopped by user.")

# Show idle message only if not fetching, not deleting, and no branches fetched yet
if not st.session_state.fetching and not st.session_state.branches_to_delete and len(branch_details) == 0:
    status_placeholder.info("Idle. Click 'Start Fetching Branches' to begin.")

# =============================
# Flicker-Free Updates
# =============================
while st.session_state.fetching or fetching_flag.is_set():
    with lock:
        # ✅ Graph with enhanced styling
        counts = {cat: len(branch_list) for cat, branch_list in branch_categories.items()}
        fig, ax = plt.subplots(figsize=(7, 5))
        
        # Enhanced color palette
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#95A5A6']
        bars = ax.bar(counts.keys(), counts.values(), color=colors, edgecolor='white', linewidth=2)
        
        ax.set_title("Branch Category Overview", fontsize=16, fontweight='bold', pad=20)
        ax.set_ylabel("Number of Branches", fontsize=12, fontweight='600')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)
        
        # Add count labels with enhanced styling
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}',
                       ha='center', va='bottom', fontsize=12, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='gray', alpha=0.8))
        
        plt.tight_layout()
        graph_placeholder.pyplot(fig)
        plt.close(fig)

        # ✅ Table with filtered branches
        active_branches = [b for b in branch_details if b["Branch"] not in st.session_state.deleted_branches]
        df = pd.DataFrame(active_branches)
        table_placeholder.dataframe(df, height=600, width='stretch')
        
        # Update status when completed
        if fetch_completed.is_set():
            st.session_state.fetching = False
            status_placeholder.success(f"✅ Completed! Total branches: {len(branch_details)}")
            break

    time.sleep(0.5)

# =============================
# Update graph and table after fetching completes (persist across checkbox clicks, refresh after deletion)
# =============================
if not st.session_state.fetching and branch_details:
    with lock:
        active_branches = [b for b in branch_details if b["Branch"] not in st.session_state.deleted_branches]
        stale_branches = [b for b in active_branches if b["Category"] == "stale"]
        open_pr_branches = [b for b in active_branches if b["Category"] == "open_pr"]
        closed_pr_branches = [b for b in active_branches if b["Category"] == "closed_pr"]
        no_pr_branches = [b for b in active_branches if b["Category"] == "no_pr"]
        
        # Filter out protected branches from all categories
        protected_branches = ['main', 'master', 'develop', 'development']
        deletable_stale = [b for b in stale_branches 
                          if b["Branch"].lower() not in protected_branches]
        deletable_open_pr = [b for b in open_pr_branches 
                            if b["Branch"].lower() not in protected_branches]
        deletable_closed_pr = [b for b in closed_pr_branches 
                              if b["Branch"].lower() not in protected_branches]
        deletable_no_pr = [b for b in no_pr_branches 
                          if b["Branch"].lower() not in protected_branches]
        
        # Store in session state to avoid recalculation on checkbox clicks
        st.session_state.active_branches = active_branches
        st.session_state.deletable_stale = deletable_stale
        st.session_state.deletable_open_pr = deletable_open_pr
        st.session_state.deletable_closed_pr = deletable_closed_pr
        st.session_state.deletable_no_pr = deletable_no_pr

        logging.info(f"session_state updated: branches_to_delete={st.session_state.branches_to_delete}, deletion_complete={st.session_state.deletion_complete}, refresh_graph={st.session_state.refresh_graph}")
        
        # Only update graph when deletion is complete or refresh flag is set
        if not st.session_state.branches_to_delete or st.session_state.deletion_complete or st.session_state.refresh_graph:
            # Update graph with current counts (active branches only)
            active_counts = {
                'stale': len(deletable_stale),
                'open_pr': len(deletable_open_pr),
                'closed_pr': len(deletable_closed_pr),
                'no_pr': len(deletable_no_pr)
            }
            logging.info(f"Active counts for graph update: {active_counts}")

            fig, ax = plt.subplots(figsize=(7, 5))
            colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#95A5A6']
            bars = ax.bar(active_counts.keys(), active_counts.values(), color=colors, edgecolor='white', linewidth=2)
            ax.set_title("Branch Category Overview", fontsize=16, fontweight='bold', pad=20)
            ax.set_ylabel("Number of Branches", fontsize=12, fontweight='600')
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.grid(axis='y', alpha=0.3, linestyle='--')
            ax.set_axisbelow(True)
            for bar in bars:
                height = bar.get_height()
                if height > 0:
                    ax.text(bar.get_x() + bar.get_width()/2., height,
                           f'{int(height)}',
                           ha='center', va='bottom', fontsize=12, fontweight='bold',
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='gray', alpha=0.8))
            plt.tight_layout()
            graph_placeholder.pyplot(fig)
            plt.close(fig)
            
            # Update table
            df = pd.DataFrame(active_branches)
            table_placeholder.dataframe(df, height=600, width='stretch')
            
            # Reset refresh flag after updating
            if st.session_state.refresh_graph:
                st.session_state.refresh_graph = False

# =============================
# Delete Buttons Section (after fetching completes)
# =============================
logging.info(f"Checking delete section: fetching={st.session_state.fetching}, branch_details count={len(branch_details)}")

delete_btn = None

if not st.session_state.fetching and branch_details:
    # Retrieve cached values from session state
    deletable_stale = st.session_state.get("deletable_stale", [])
    deletable_open_pr = st.session_state.get("deletable_open_pr", [])
    deletable_closed_pr = st.session_state.get("deletable_closed_pr", [])
    deletable_no_pr = st.session_state.get("deletable_no_pr", [])
    
    logging.info(f"Found {len(deletable_stale)} deletable stale branches, {len(deletable_open_pr)} open PR branches, {len(deletable_closed_pr)} closed PR branches, {len(deletable_no_pr)} no PR branches")
    
    # Build radio options based on available branches
    delete_options = []
    if deletable_stale:
        delete_options.append(f"Stale Branches ({len(deletable_stale)})")
    if deletable_open_pr:
        delete_options.append(f"Open PR Branches ({len(deletable_open_pr)})")
    if deletable_closed_pr:
        delete_options.append(f"Closed PR Branches ({len(deletable_closed_pr)})")
    if deletable_no_pr:
        delete_options.append(f"No PR Branches ({len(deletable_no_pr)})")
    
    # Show common selection section in right column
    if delete_options:
        with selection_placeholder.container():
            st.markdown("#### 📌 Select Branch Categories")
            st.caption("Choose categories for deletion or notification:")
            
            # Show checkboxes for available categories with enhanced styling
            if deletable_stale:
                st.session_state.delete_stale = st.checkbox(
                    f"Stale Branches ({len(deletable_stale)})",
                    key="checkbox_stale",
                    help=f"Branches with no commits for {stale_days}+ days"
                )
            
            if deletable_open_pr:
                st.session_state.delete_open_pr = st.checkbox(
                    f"Open PR Branches ({len(deletable_open_pr)})",
                    key="checkbox_open_pr",
                    help="⚠️ Protected from deletion - available for notification only"
                )
            
            if deletable_closed_pr:
                st.session_state.delete_closed_pr = st.checkbox(
                    f"Closed PR Branches ({len(deletable_closed_pr)})",
                    key="checkbox_closed_pr",
                    help="Branches associated with closed pull requests"
                )
            
            if deletable_no_pr:
                st.session_state.delete_no_pr = st.checkbox(
                    f"No PR Branches ({len(deletable_no_pr)})",
                    key="checkbox_no_pr",
                    help="Branches without any associated pull requests"
                )
        
# Handle delete button click
if delete_btn:
    # Determine which branches to delete based on checkbox selections
    branches_to_queue = []
    selected_categories = []
    protected_branches = ['main', 'master', 'develop', 'development']
    
    if st.session_state.delete_stale:
        # Filter out protected branches
        safe_stale = [b for b in deletable_stale if b["Branch"].lower() not in protected_branches]
        branches_to_queue.extend(safe_stale)
        selected_categories.append(f"Stale ({len(safe_stale)})")
        logging.info(f"🔴 User selected 'Stale Branches' - queuing {len(safe_stale)} branches")
        print(f"\n🔴 Queuing {len(safe_stale)} stale branches for deletion")
    
    # Open PR branches are protected - skip deletion
    if st.session_state.get('delete_open_pr', False):
        logging.warning("⚠️ Attempted to delete Open PR branches - skipped (protected)")
        st.warning("⚠️ Open PR branches are protected and cannot be deleted")
    
    if st.session_state.delete_closed_pr:
        # Filter out protected branches
        safe_closed_pr = [b for b in deletable_closed_pr if b["Branch"].lower() not in protected_branches]
        branches_to_queue.extend(safe_closed_pr)
        selected_categories.append(f"Closed PR ({len(safe_closed_pr)})")
        logging.info(f"🔴 User selected 'Closed PR Branches' - queuing {len(safe_closed_pr)} branches")
        print(f"\n🔴 Queuing {len(safe_closed_pr)} closed PR branches for deletion")
    
    if st.session_state.delete_no_pr:
        # Filter out protected branches
        safe_no_pr = [b for b in deletable_no_pr if b["Branch"].lower() not in protected_branches]
        branches_to_queue.extend(safe_no_pr)
        selected_categories.append(f"No PR ({len(safe_no_pr)})")
        logging.info(f"🔴 User selected 'No PR Branches' - queuing {len(safe_no_pr)} branches")
        print(f"\n🔴 Queuing {len(safe_no_pr)} no PR branches for deletion")
    
    if branches_to_queue:
        logging.info(f"🔴 BUTTON CLICKED! Deleting categories: {', '.join(selected_categories)}")
        
        # Reset deletion_complete flag when starting new deletion
        st.session_state.deletion_complete = False
        
        # Queue branches for deletion (with additional protected branch check)
        for branch in branches_to_queue:
            if branch["Branch"].lower() not in protected_branches and branch["Category"] != "open_pr":
                st.session_state.branches_to_delete.add(branch["Branch"])
                logging.debug(f"Queued for deletion: {branch['Branch']}")
            else:
                logging.warning(f"⚠️ Skipped protected branch: {branch['Branch']}")
        logging.info(f"Total branches queued for deletion: {len(st.session_state.branches_to_delete)}")
        
        # Reset checkboxes after queuing
        st.session_state.delete_stale = False
        st.session_state.delete_open_pr = False
        st.session_state.delete_closed_pr = False
        st.session_state.delete_no_pr = False
    else:
        st.warning("Please select at least one category to delete.")

# Handle notification button click
if notify_btn:
    logging.info(f"📧 Notification button clicked - Creating GitHub issue")
    
    # Collect selected branches for notification
    branches_to_notify = []
    
    if st.session_state.delete_stale:
        branches_to_notify.extend(deletable_stale)
        logging.info(f"Selected stale branches for notification: {len(deletable_stale)}")
    
    if st.session_state.delete_open_pr:
        branches_to_notify.extend(deletable_open_pr)
        logging.info(f"Selected open PR branches for notification: {len(deletable_open_pr)}")
    
    if st.session_state.delete_closed_pr:
        branches_to_notify.extend(deletable_closed_pr)
        logging.info(f"Selected closed PR branches for notification: {len(deletable_closed_pr)}")
    
    if st.session_state.delete_no_pr:
        branches_to_notify.extend(deletable_no_pr)
        logging.info(f"Selected no PR branches for notification: {len(deletable_no_pr)}")
    
    if not branches_to_notify:
        logging.warning("⚠️ No branches selected for notification")
        st.warning("Please select at least one category to notify about.")
    else:
        logging.info(f"Total branches to notify about: {len(branches_to_notify)}")
        
        # Prepare notification data
        branches_by_author = prepare_notification_data(branches_to_notify)
        logging.info(f"Branches grouped by {len(branches_by_author)} authors")
        
        # Create GitHub issue with @mentions
        logging.info("🔔 Creating GitHub issue with @mentions")
        with st.spinner("Creating GitHub issue with notifications..."):
            issue_title = f"🔔 Stale Branch Cleanup - {len(branches_to_notify)} branches need attention"
            logging.info(f"Issue title: {issue_title}")
            
            success, message, issue_url = create_github_issue_notification(
                github_token, owner, repo, branches_by_author, issue_title
            )
            
            if success:
                with notification_result_placeholder.container():
                    st.success(f"✅ {message}")
                    if issue_url:
                        st.markdown(f"**[View Issue]({issue_url})** - Authors will be notified via GitHub")
                        st.info("💡 Users mentioned with @username will receive GitHub notifications automatically")
                logging.info(f"✅ GitHub issue created: {issue_url}")
            else:
                with notification_result_placeholder.container():
                    st.error(f"❌ {message}")
                logging.error(f"❌ Failed to create GitHub issue: {message}")


# =============================
# Handle branch deletion (runs on every rerun if queue is not empty)
# =============================
if st.session_state.branches_to_delete and not st.session_state.fetching:
    branches_list = list(st.session_state.branches_to_delete)
    
    # Update status message
    status_placeholder.warning(f"🗑️ Deleting {len(branches_list)} branches...")
    
    # Print list of branches to be deleted
    print(f"\n{'='*60}")
    print(f"BRANCHES TO BE DELETED ({len(branches_list)})")
    print(f"{'='*60}")
    for i, branch_name in enumerate(branches_list, 1):
        print(f"{i}. {branch_name}")
    print(f"{'='*60}\n")
    
    logging.info(f"Starting deletion of {len(branches_list)} branches")
    
    with st.spinner(f"Deleting {len(branches_list)} branches..."):
        deleted, failed = delete_branches(github_token, owner, repo, branches_list)
        st.session_state.branches_to_delete.clear()
    
    status_placeholder.success(f"✅ Deleted {deleted} branches, {failed} failed")
    
    # Set flags and immediately update graph and table
    st.session_state.deletion_complete = True
    st.session_state.refresh_graph = True
    
    # Force immediate update of graph and table
    with lock:
        active_branches = [b for b in branch_details if b["Branch"] not in st.session_state.deleted_branches]
        
        # Filter out protected branches
        protected_branches = ['main', 'master', 'develop', 'development']
        stale_branches = [b for b in active_branches if b["Category"] == "stale"]
        open_pr_branches = [b for b in active_branches if b["Category"] == "open_pr"]
        closed_pr_branches = [b for b in active_branches if b["Category"] == "closed_pr"]
        no_pr_branches = [b for b in active_branches if b["Category"] == "no_pr"]
        
        deletable_stale = [b for b in stale_branches if b["Branch"].lower() not in protected_branches]
        deletable_open_pr = [b for b in open_pr_branches if b["Branch"].lower() not in protected_branches]
        deletable_closed_pr = [b for b in closed_pr_branches if b["Branch"].lower() not in protected_branches]
        deletable_no_pr = [b for b in no_pr_branches if b["Branch"].lower() not in protected_branches]
        
        # Update counts
        active_counts = {
            'stale': len(deletable_stale),
            'open_pr': len(deletable_open_pr),
            'closed_pr': len(deletable_closed_pr),
            'no_pr': len(deletable_no_pr)
        }
        
        logging.info(f"Post-deletion graph update with counts: {active_counts}")
        
        # Update graph immediately with enhanced styling
        fig, ax = plt.subplots(figsize=(7, 5))
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#95A5A6']
        bars = ax.bar(active_counts.keys(), active_counts.values(), color=colors, edgecolor='white', linewidth=2)
        ax.set_title("Branch Category Overview", fontsize=16, fontweight='bold', pad=20)
        ax.set_ylabel("Number of Branches", fontsize=12, fontweight='600')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}',
                       ha='center', va='bottom', fontsize=12, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='gray', alpha=0.8))
        plt.tight_layout()
        graph_placeholder.pyplot(fig)
        plt.close(fig)
        
        # Update table immediately
        df = pd.DataFrame(active_branches)
        table_placeholder.dataframe(df, height=600, width='stretch')

    time.sleep(1)
