import requests
import streamlit as st
import matplotlib.pyplot as plt
from datetime import datetime, timezone
import urllib3
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================
# Email Notification Function
# =============================
def send_email_notification(sender_email, sender_password, recipients, smtp_server, smtp_port, stale_branch_details):
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = "Stale Branch Cleanup Notification"

        html_body = f"<h3>Stale Branches ({len(stale_branch_details)})</h3><ul>"
        html_body += "".join([f"<li>{author} ({email}) : {branch}</li>" for author, email, branch in stale_branch_details])
        html_body += "</ul>"

        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())

        return True, f"Email sent to {', '.join(recipients)}"
    except Exception as e:
        return False, str(e)

# =============================
# Streamlit UI
# =============================
st.title("GitHub Branch Statistics Dashboard")

# Inputs
github_token = st.text_input("Enter your GitHub Token", type="password")
owner = st.text_input("Repository Owner", value="etn-utilities")
repo = st.text_input("Repository Name", value="yuk-yukon")
stale_days = st.number_input("Stale Branch Threshold (days)", min_value=1, value=90)

smtp_server = st.text_input("SMTP Server (e.g., smtp.gmail.com)")
smtp_port = st.number_input("SMTP Port", value=587)
sender_email = st.text_input("Sender Email")
sender_password = st.text_input("Sender Email Password", type="password")

# =============================
# Fetch Branch Stats
# =============================
if st.button("Fetch Branch Stats"):
    if not github_token:
        st.error("Please provide your GitHub token.")
    else:
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches"
        prs_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=all&per_page=100"

        # Fetch branches with pagination
        branches = []
        page = 1
        while True:
            url = f"{branches_url}?per_page=100&page={page}"
            response = requests.get(url, headers=headers, verify=False)
            if response.status_code != 200:
                st.error(f"Failed to fetch branches: {response.text}")
                break
            data = response.json()
            if not data or isinstance(data, dict):
                break
            branches.extend(data)
            page += 1

        # Fetch PRs
        prs_response = requests.get(prs_url, headers=headers, verify=False)
        if prs_response.status_code != 200:
            st.error(f"Failed to fetch PRs: {prs_response.text}")
            prs = []
        else:
            prs = prs_response.json()

        # Categorize branches
        branch_categories = {"stale": [], "open_pr": [], "closed_pr": [], "no_pr": []}
        pr_map = {pr['head']['ref']: 'open_pr' if pr['state'] == 'open' else 'closed_pr' for pr in prs}

        now = datetime.now(timezone.utc)
        branch_details = []
        stale_authors = set()

        for branch in branches:
            name = branch['name']
            commit_url = branch['commit']['url']
            commit_data = requests.get(commit_url, headers=headers, verify=False).json()
            commit_date_str = commit_data['commit']['author']['date']
            commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            author_name = commit_data['commit']['author'].get('name', 'Unknown')
            author_email = commit_data['commit']['author'].get('email', 'Unknown')

            # Stale logic
            if (now - commit_date).days > stale_days:
                category = "stale"
                if author_email != 'Unknown':
                    stale_authors.add(author_email)
            else:
                category = pr_map.get(name, "no_pr")

            branch_categories[category].append((author_name, author_email, name))
            branch_details.append({
                "Branch": name,
                "Last Commit": commit_date.strftime("%Y-%m-%d"),
                "Category": category,
                "Author": author_name,
                "Author Email": author_email
            })

        # Display counts
        counts = {cat: len(branch_list) for cat, branch_list in branch_categories.items()}
        st.subheader("Branch Category Counts")
        st.write(counts)

        # Charts
        fig, ax = plt.subplots()
        ax.bar(counts.keys(), counts.values(), color=['orange', 'green', 'blue', 'gray'])
        ax.set_title("Branch Categories")
        st.pyplot(fig)

        fig2, ax2 = plt.subplots()
        ax2.pie(counts.values(), labels=counts.keys(), autopct='%1.1f%%', colors=['orange', 'green', 'blue', 'gray'])
        st.pyplot(fig2)

        # Branch details
        st.subheader("Branches by Category (Author : Email : Branch)")
        for cat, branch_list in branch_categories.items():
            st.markdown(f"### {cat.upper()} ({len(branch_list)})")
            if branch_list:
                st.markdown("<ul>" + "".join([f"<li>{author} : {email} : {branch}</li>" for author, email, branch in branch_list]) + "</ul>", unsafe_allow_html=True)
            else:
                st.write("No branches in this category.")

        # Stale authors
        st.subheader("Stale Branch Authors")
        st.write(", ".join(stale_authors) if stale_authors else "No stale branch authors found.")

        # CSV Export
        df = pd.DataFrame(branch_details)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Download Branch Details as CSV", csv, "branch_details.csv", "text/csv")

        # Email Notification
        if st.button("Send Email Notification"):
            if not sender_email or not sender_password or not smtp_server:
                st.error("Please fill all email fields.")
            elif not stale_authors:
                st.error("No stale branch authors to notify.")
            else:
                success, message = send_email_notification(sender_email, sender_password, list(stale_authors), smtp_server, smtp_port, branch_categories['stale'])
                st.success(message) if success else st.error(f"Failed to send email: {message}")