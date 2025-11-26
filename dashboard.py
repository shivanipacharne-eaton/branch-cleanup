import httpx
import streamlit as st
import matplotlib.pyplot as plt
from datetime import datetime, timezone
import threading
import time
import pandas as pd
import logging

# ✅ Enable wide layout
st.set_page_config(layout="wide")

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Initialize session state
if "fetching" not in st.session_state:
    st.session_state.fetching = False

# Shared data
branch_details = []
branch_categories = {"stale": [], "open_pr": [], "closed_pr": [], "no_pr": []}
lock = threading.Lock()
total_pages_estimate = 50

fetching_flag = threading.Event()
fetch_completed = threading.Event()

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
        url = f"{branches_url}?per_page=10&page={page}"
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
        time.sleep(1)

    client.close()
    fetching_flag.clear()  # ✅ Stop flag when done
    fetch_completed.set()  # Signal that fetching is complete

# =============================
# UI Layout
# =============================
st.title("Real-Time GitHub Branch Dashboard")

col_left, col_center, col_right = st.columns([1, 3, 3])

# ✅ Left Column: Inputs & Buttons
with col_left:
    github_token = st.text_input("GitHub Token", type="password", value="")
    owner = st.text_input("Repository Owner", value="etn-utilities")
    repo = st.text_input("Repository Name", value="yuk-yukon")
    stale_days = st.number_input("Stale Branch Threshold (days)", min_value=1, value=90)

    if not st.session_state.fetching:
        start_btn = st.button("Start Fetching Branches")
    else:
        start_btn = None
    stop_btn = st.button("Stop Fetching")

    status_placeholder = st.empty()

# ✅ Center Column: Graph
with col_center:
    graph_placeholder = st.empty()

# ✅ Right Column: Table
with col_right:
    st.markdown("<br>" * 1, unsafe_allow_html=True)  # Vertical spacing
    table_placeholder = st.empty()

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

# =============================
# Flicker-Free Updates
# =============================
while st.session_state.fetching or fetching_flag.is_set():
    with lock:
        # ✅ Graph
        counts = {cat: len(branch_list) for cat, branch_list in branch_categories.items()}
        fig, ax = plt.subplots(figsize=(6, 4))
        bars = ax.bar(counts.keys(), counts.values(), color=['orange', 'green', 'blue', 'gray'])
        ax.set_title("Live Branch Category Summary")
        
        # Add count labels on top of each bar
        for bar in bars:
            height = bar.get_height()
            if height > 0:  # Only show label if there are branches
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}',
                       ha='center', va='bottom', fontsize=10, fontweight='bold')
        
        graph_placeholder.pyplot(fig)
        plt.close(fig)

        # ✅ Table (no filter)
        df = pd.DataFrame(branch_details)
        table_placeholder.dataframe(df, height=550)
        
        # Update status when completed
        if fetch_completed.is_set():
            st.session_state.fetching = False
            status_placeholder.success(f"✅ Completed! Total branches: {len(branch_details)}")
            break

    time.sleep(1)