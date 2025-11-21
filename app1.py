import requests
import streamlit as st
import matplotlib.pyplot as plt
from datetime import datetime, timezone
import threading
import time
import urllib3
import pandas as pd
import logging

# ✅ Enable wide layout
st.set_page_config(layout="wide")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# =============================
# Background Fetch Function (NO Streamlit calls)
# =============================
def fetch_branches_continuously(token, owner, repo, stale_days):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches"
    prs_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=all&per_page=100"

    # Fetch all PRs first
    prs_response = requests.get(prs_url, headers=headers, verify=False)
    pr_map = {}
    if prs_response.status_code == 200:
        prs = prs_response.json()
        pr_map = {pr['head']['ref']: 'open_pr' if pr['state'] == 'open' else 'closed_pr' for pr in prs}

    page = 1
    while fetching_flag.is_set():
        url = f"{branches_url}?per_page=100&page={page}"
        logging.info(f"Fetching page {page} from GitHub API...")
        response = requests.get(url, headers=headers, verify=False)

        if response.status_code != 200 or not response.json() or isinstance(response.json(), dict):
            logging.info("No more pages to fetch. Pagination complete.")
            break

        data = response.json()
        now = datetime.now(timezone.utc)

        with lock:
            for branch in data:
                name = branch['name']
                commit_url = branch['commit']['url']
                commit_data = requests.get(commit_url, headers=headers, verify=False).json()
                commit_date_str = commit_data['commit']['author']['date']
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

    fetching_flag.clear()  # ✅ Stop flag when done

# =============================
# UI Layout
# =============================
st.title("Real-Time GitHub Branch Dashboard")

col_left, col_center, col_right = st.columns([1, 3, 3])

# ✅ Left Column: Inputs & Buttons
with col_left:
    github_token = st.text_input("GitHub Token", type="password", value="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")
    owner = st.text_input("Repository Owner", value="etn-utilities")
    repo = st.text_input("Repository Name", value="yuk-yukon")
    stale_days = st.number_input("Stale Branch Threshold (days)", min_value=1, value=90)

    if not st.session_state.fetching:
        start_btn = st.button("Start Fetching Branches")
    else:
        start_btn = None
    stop_btn = st.button("Stop Fetching", disabled=not st.session_state.fetching)

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
        ax.bar(counts.keys(), counts.values(), color=['orange', 'green', 'blue', 'gray'])
        ax.set_title("Live Branch Category Summary")
        graph_placeholder.pyplot(fig)
        plt.close(fig)

        # ✅ Table (no filter)
        df = pd.DataFrame(branch_details)
        table_placeholder.dataframe(df, height=550)

    time.sleep(1)

status_placeholder.success(f"✅ Completed fetching! Total branches: {len(branch_details)}")

# ✅ Completion message
if not fetching_flag.is_set() and branch_details:
    st.session_state.fetching = False
    with col_left:
        status_placeholder.success(f"✅ Completed fetching! Total branches: {len(branch_details)}")