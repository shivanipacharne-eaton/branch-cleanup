import requests
import streamlit as st
import matplotlib.pyplot as plt
from datetime import datetime, timezone
import threading
import time
import urllib3
import pandas as pd
import logging

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Shared data
branch_details = []
branch_categories = {"stale": [], "open_pr": [], "closed_pr": [], "no_pr": []}
lock = threading.Lock()
fetching = False
pages_fetched = 0
total_pages_estimate = 50  # ✅ Estimated max pages for progress bar

# =============================
# Background Fetch Function
# =============================
def fetch_branches_continuously(token, owner, repo, stale_days):
    global fetching, pages_fetched
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches"
    prs_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=all&per_page=100"

    # Fetch PRs once
    prs_response = requests.get(prs_url, headers=headers, verify=False)
    pr_map = {}
    if prs_response.status_code == 200:
        prs = prs_response.json()
        pr_map = {pr['head']['ref']: 'open_pr' if pr['state'] == 'open' else 'closed_pr' for pr in prs}

    page = 1
    while fetching:
        url = f"{branches_url}?per_page=100&page={page}"
        logging.info(f"Fetching page {page} from GitHub API...")
        response = requests.get(url, headers=headers, verify=False)
        if response.status_code != 200:
            logging.error(f"Failed to fetch page {page}: {response.text}")
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
                commit_data = requests.get(commit_url, headers=headers, verify=False).json()
                commit_date_str = commit_data['commit']['author']['date']
                commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                author_name = commit_data['commit']['author'].get('name', 'Unknown')
                author_email = commit_data['commit']['author'].get('email', 'Unknown')

                # Categorize
                category = "stale" if (now - commit_date).days > stale_days else pr_map.get(name, "no_pr")
                branch_categories[category].append((author_name, author_email, name))
                branch_details.append({
                    "Branch": name,
                    "Last Commit": commit_date.strftime("%Y-%m-%d"),
                    "Category": category,
                    "Author": author_name,
                    "Author Email": author_email
                })

        pages_fetched = page
        page += 1
        time.sleep(2)

    fetching = False  # ✅ Signal completion

# =============================
# Streamlit UI
# =============================
st.title("Real-Time GitHub Branch Dashboard")

github_token = st.text_input("Enter your GitHub Token", type="password", value="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")
owner = st.text_input("Repository Owner", value="etn-utilities")
repo = st.text_input("Repository Name", value="yuk-yukon")
stale_days = st.number_input("Stale Branch Threshold (days)", min_value=1, value=90)

start_btn = st.button("Start Fetching Branches")
stop_btn = st.button("Stop Fetching")

status_placeholder = st.empty()
progress_bar = st.progress(0)
graph_placeholder = st.empty()
table_placeholder = st.empty()

if start_btn and github_token:
    fetching = True
    pages_fetched = 0
    status_placeholder.info("Fetching branches in background...")
    threading.Thread(target=fetch_branches_continuously, args=(github_token, owner, repo, stale_days), daemon=True).start()

if stop_btn:
    fetching = False
    status_placeholder.empty()
    st.warning("Fetching stopped by user.")

# ✅ Live update loop
while fetching or branch_details:
    with lock:
        # ✅ Update progress bar
        progress = min(pages_fetched / total_pages_estimate, 1.0)
        progress_bar.progress(progress)

        counts = {cat: len(branch_list) for cat, branch_list in branch_categories.items()}

        # ✅ Update graph
        fig, ax = plt.subplots()
        ax.bar(counts.keys(), counts.values(), color=['orange', 'green', 'blue', 'gray'])
        ax.set_title("Live Branch Category Summary")
        graph_placeholder.pyplot(fig)
        plt.close(fig)

        # ✅ Update table (show all details)
        df = pd.DataFrame(branch_details)
        table_placeholder.write(df)

    if not fetching:
        break
    time.sleep(3)

# ✅ After fetching completes
if not fetching and branch_details:
    status_placeholder.empty()
    progress_bar.progress(1.0)
    st.success(f"✅ Completed fetching all pages! Total pages fetched: {pages_fetched}")