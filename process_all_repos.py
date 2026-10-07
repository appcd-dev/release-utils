#!/usr/bin/env python3
"""
Process All Repositories from input.json

This script reads input.json, calls fetchTicketChangesInBuildsForRepo.py for each
repository that has version changes, and generates a consolidated JSON output with
all Linear tickets found across all services.

Usage:
    python process_all_repos.py
    python process_all_repos.py --input input.json --output changes.json
    python process_all_repos.py --skip-unchanged
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
import requests


class RepositoryProcessor:
    """Process multiple repositories and extract Linear tickets from version changes."""
    
    def __init__(self, skip_unchanged: bool = True, verbose: bool = False,
                 fetch_linear_details: bool = True, commit_diff_log_path: Optional[str] = None):
        """
        Initialize the processor.

        Args:
            skip_unchanged: Skip repositories where current_tag == new_tag
            verbose: Enable verbose output
            fetch_linear_details: Whether to fetch ticket details from Linear API
            commit_diff_log_path: If set, write full commit diff with messages to this file
        """
        self.skip_unchanged = skip_unchanged
        self.verbose = verbose
        self.fetch_linear_details = fetch_linear_details
        self.commit_diff_log_path = commit_diff_log_path
        # Match ticket IDs: [PROJ-123] or PROJ-123 at word boundary (e.g. "PLAT-1794 |", "OPS-219:")
        self.ticket_pattern = re.compile(
            r'\[([A-Z]{2,6}-\d{1,6})\]'
            r'|(?:^|[\s(])([A-Z]{2,6}-\d{1,6})(?=[\s:|\)\]\-,]|$)',
            re.MULTILINE
        )
        self.linear_api_key = os.getenv('LINEAR_API_KEY')
        self.linear_api_url = "https://api.linear.app/graphql"

    @staticmethod
    def _linear_issue_browser_url(ticket_id: str) -> str:
        """Public web URL for an issue id (used when GraphQL returns HTTP 400)."""
        ws = (os.getenv('LINEAR_WEB_WORKSPACE') or 'stackgen').strip().strip('/')
        if not ws:
            ws = 'stackgen'
        return f'https://linear.app/{ws}/issue/{ticket_id}'

    def _unresolved_issue_placeholder(self, ticket_id: str) -> Dict[str, object]:
        """Placeholder when the API cannot resolve the issue id (HTTP 400 or GraphQL issue not found)."""
        return {
            'id': ticket_id,
            'title': '',
            'state': 'Unknown',
            'priority': 0,
            'assignee': 'Unassigned',
            'projectId': 'No Project',
            'projectName': 'No Project',
            'issueUrl': self._linear_issue_browser_url(ticket_id),
            'fetchHttpStatus': '400',
        }

    @staticmethod
    def _graphql_errors_indicate_issue_not_found(errors: object) -> bool:
        """
        Linear often returns HTTP 200 with errors[].extensions.statusCode 400 for
        missing/legacy identifiers (e.g. AE-1952) while the browser URL still resolves.
        """
        if not isinstance(errors, list):
            return False
        for err in errors:
            if not isinstance(err, dict):
                continue
            path = err.get('path')
            if not isinstance(path, list) or not path or path[-1] != 'issue':
                continue
            ext = err.get('extensions')
            ext = ext if isinstance(ext, dict) else {}
            if ext.get('statusCode') == 400:
                return True
            if ext.get('code') == 'INPUT_ERROR':
                return True
            msg = str(err.get('message', '')).lower()
            if 'not found' in msg and 'issue' in msg:
                return True
        return False

    # Linear states omitted from aggregated tag-diff ticket outputs.
    _OMITTED_TICKET_STATES = frozenset({
        'canceled',
        'cancelled',
        'in progress',
    })

    @staticmethod
    def _should_omit_ticket(details: Optional[Dict[str, str]]) -> bool:
        """True when Linear workflow state should be skipped in release artifacts."""
        if not details:
            return False
        state = str(details.get('state') or '').strip().lower()
        return state in RepositoryProcessor._OMITTED_TICKET_STATES

    @staticmethod
    def _priority_sort_rank(priority: object) -> int:
        """
        Sort rank for Linear priority values.

        Linear: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low.
        Urgent first; no priority last.
        """
        try:
            value = int(priority)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else 99

    def extract_repo_path(self, repo_url: str) -> Optional[str]:
        """
        Extract owner/repo path from GitHub URL.
        
        Args:
            repo_url: Full GitHub repository URL
            
        Returns:
            Repository path in format 'owner/repo' or None if invalid
        """
        try:
            # Parse the URL
            parsed = urlparse(repo_url)
            # Extract path and remove leading/trailing slashes
            path = parsed.path.strip('/')
            # Remove .git suffix if present
            if path.endswith('.git'):
                path = path[:-4]
            return path
        except Exception as e:
            print(f"Error parsing repository URL '{repo_url}': {e}", file=sys.stderr)
            return None
    
    def call_compare_tags(self, repo: str, from_tag: str, to_tag: str) -> str:
        """
        Call compare_tags.py directly and return its output.
        
        Args:
            repo: Repository in format 'owner/repo'
            from_tag: Starting tag
            to_tag: Ending tag
            
        Returns:
            Output from compare_tags.py as string
        """
        cmd = [
            sys.executable,
            'compare_tags.py',
            repo,
            from_tag,
            to_tag
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent,
                timeout=120  # 2 minute timeout
            )
            
            if result.returncode != 0:
                if self.verbose:
                    print(f"  ⚠️  Warning: compare_tags.py returned error: {result.stderr}", file=sys.stderr)
                return ""
            
            return result.stdout
            
        except subprocess.TimeoutExpired:
            print(f"  ⚠️  Warning: Timeout fetching changes for {repo}", file=sys.stderr)
            return ""
        except FileNotFoundError:
            print("Error: compare_tags.py not found", file=sys.stderr)
            return ""
        except Exception as e:
            print(f"  ⚠️  Warning: Error calling compare_tags.py for {repo}: {e}", file=sys.stderr)
            return ""
    
    def extract_tickets_from_text(self, text: str) -> Set[str]:
        """
        Extract Linear tickets from text.
        
        Args:
            text: Text containing commit messages
            
        Returns:
            Set of unique Linear tickets found
        """
        tickets = set()
        matches = self.ticket_pattern.findall(text)
        for match in matches:
            # match is a tuple when pattern has multiple groups: (bracketed, unbracketed)
            for g in (match if isinstance(match, tuple) else (match,)):
                if g:
                    tickets.add(g)
        return tickets
    
    def fetch_ticket_details(self, ticket_id: str) -> Optional[Dict[str, str]]:
        """
        Fetch ticket details from Linear API.
        
        Args:
            ticket_id: Linear ticket ID (e.g., 'PROJ-123')
            
        Returns:
            Dictionary with ticket details or None if fetch fails
        """
        if not self.linear_api_key:
            return None
        
        query = """
        query IssueByIdentifier($identifier: String!) {
            issue(id: $identifier) {
                id
                identifier
                title
                state {
                    name
                }
                priority
                assignee {
                    name
                }
                project {
                    id
                    name
                }
            }
        }
        """
        
        headers = {
            "Authorization": self.linear_api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "variables": {"identifier": ticket_id}
        }
        
        try:
            response = requests.post(
                self.linear_api_url,
                headers=headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 400:
                return self._unresolved_issue_placeholder(ticket_id)
            
            if response.status_code == 200:
                data = response.json()
                
                if 'errors' in data:
                    errs = data.get('errors')
                    if self._graphql_errors_indicate_issue_not_found(errs):
                        return self._unresolved_issue_placeholder(ticket_id)
                    return None
                
                if 'data' in data and data['data'].get('issue'):
                    issue = data['data']['issue']
                    return {
                        'id': issue['identifier'],
                        'title': issue['title'],
                        'state': issue['state']['name'] if issue.get('state') else 'Unknown',
                        'priority': issue.get('priority', 0),
                        'assignee': issue['assignee']['name'] if issue.get('assignee') else 'Unassigned',
                        'projectId': issue['project']['id'] if issue.get('project') else 'No Project',
                        'projectName': issue['project']['name'] if issue.get('project') else 'No Project'
                    }
            
            return None
        except Exception:
            return None
    
    def fetch_all_ticket_details(self, tickets: Set[str]) -> Dict[str, Optional[Dict[str, str]]]:
        """
        Fetch details for all tickets from Linear API.
        
        Args:
            tickets: Set of ticket IDs
            
        Returns:
            Dictionary mapping ticket IDs to their details
        """
        if not self.fetch_linear_details or not self.linear_api_key:
            return {ticket: None for ticket in tickets}
        
        ticket_details = {}
        total = len(tickets)
        
        if self.verbose and total > 0:
            print(f"\n📋 Fetching Linear ticket details for {total} unique tickets...")
        
        for idx, ticket in enumerate(sorted(tickets), 1):
            if self.verbose and total > 0:
                print(f"  [{idx}/{total}] Fetching {ticket}...", end='\r')
            
            details = self.fetch_ticket_details(ticket)
            ticket_details[ticket] = details
        
        if self.verbose and total > 0:
            successful = sum(1 for d in ticket_details.values() if d is not None)
            print(f"\n  ✅ Successfully fetched {successful}/{total} ticket details\n")
        
        return ticket_details
    
    def fetch_project_details(self, project_id: str) -> Optional[Dict[str, str]]:
        """
        Fetch project details from Linear API.
        
        Args:
            project_id: Linear project ID
            
        Returns:
            Dictionary with project details or None if fetch fails
        """
        if not self.linear_api_key or project_id == 'No Project':
            return None
        
        query = """
        query ProjectById($id: String!) {
            project(id: $id) {
                id
                name
                url
                description
                state
                progress
            }
        }
        """
        
        headers = {
            "Authorization": self.linear_api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "variables": {"id": project_id}
        }
        
        try:
            response = requests.post(
                self.linear_api_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if 'errors' in data:
                    return None
                
                if 'data' in data and data['data'].get('project'):
                    project = data['data']['project']
                    return {
                        'id': project['id'],
                        'name': project['name'],
                        'url': project.get('url', ''),
                        'description': project.get('description', ''),
                        'state': project.get('state', 'Unknown'),
                        'progress': project.get('progress', 0)
                    }
            
            return None
        except Exception:
            return None
    
    def fetch_all_project_details(self, project_ids: Set[str]) -> Dict[str, Optional[Dict[str, str]]]:
        """
        Fetch details for all projects from Linear API.
        
        Args:
            project_ids: Set of project IDs
            
        Returns:
            Dictionary mapping project IDs to their details
        """
        if not self.fetch_linear_details or not self.linear_api_key:
            return {project_id: None for project_id in project_ids}
        
        project_details = {}
        total = len(project_ids)
        
        if self.verbose and total > 0:
            print(f"\n🏗️  Fetching Linear project details for {total} unique projects...")
        
        for idx, project_id in enumerate(sorted(project_ids), 1):
            if self.verbose and total > 0:
                print(f"  [{idx}/{total}] Fetching project {project_id}...", end='\r')
            
            details = self.fetch_project_details(project_id)
            project_details[project_id] = details
        
        if self.verbose and total > 0:
            successful = sum(1 for d in project_details.values() if d is not None)
            print(f"\n  ✅ Successfully fetched {successful}/{total} project details\n")
        
        return project_details
    
    def should_process_service(self, service: Dict) -> bool:
        """
        Determine if a service should be processed.
        
        Args:
            service: Service dictionary from input.json
            
        Returns:
            True if service should be processed
        """
        current = service.get('current_tag', '')
        new = service.get('new_tag', '')
        
        # Skip if both tags are empty
        if not current and not new:
            return False
        
        # Skip if tags are the same and skip_unchanged is True
        if self.skip_unchanged and current == new:
            return False
        
        return True
    
    def process_service(self, service: Dict) -> Optional[Dict]:
        """
        Process a single service and extract ticket changes.
        
        Args:
            service: Service dictionary from input.json
            
        Returns:
            Dictionary with service info and tickets, or None if failed
        """
        service_name = service.get('service', 'unknown')
        repo_url = service.get('repository', '')
        current_tag = service.get('current_tag', '')
        new_tag = service.get('new_tag', '')
        version_key = service.get('version_key', '')
        
        # Extract repo path
        repo_path = self.extract_repo_path(repo_url)
        if not repo_path:
            print(f"  ❌ Invalid repository URL for {service_name}", file=sys.stderr)
            return None
        
        print(f"  📦 Processing {service_name} ({current_tag} → {new_tag})...")
        print(f"     Comparing 2 tags for repository: {repo_path} (service: {service_name})")
        print(f"     From tag: {current_tag}  →  To tag: {new_tag}")
        print(f"     Fetching commit messages between these tags...")
        
        # Handle empty current_tag - might be a new service
        if not current_tag and new_tag:
            print(f"     ℹ️  New service detected, skipping (no previous version to compare)")
            return {
                'service': service_name,
                'repository': repo_url,
                'repo_path': repo_path,
                'version_key': version_key,
                'current_tag': current_tag,
                'new_tag': new_tag,
                'status': 'new_service',
                'tickets': [],
                'ticket_count': 0,
                'error': None
            }
        
        # Handle empty new_tag
        if not new_tag:
            print(f"     ℹ️  No new version specified, skipping")
            return {
                'service': service_name,
                'repository': repo_url,
                'repo_path': repo_path,
                'version_key': version_key,
                'current_tag': current_tag,
                'new_tag': new_tag,
                'status': 'no_new_version',
                'tickets': [],
                'ticket_count': 0,
                'error': None
            }
        
        # --- Log git commit changes: fetch comparison and optionally write to file ---
        # Call compare_tags.py to get full diff between current_tag and new_tag (summary, commit list, full messages, file changes)
        output = self.call_compare_tags(repo_path, current_tag, new_tag)
        
        if output:
            # Log git commit changes to stdout (each repo's comparison and commit messages)
            # print(f"\n     --- Repository: {repo_path} | Tags: {current_tag} → {new_tag} | Commit messages between tags ---")
            # for line in output.splitlines():
            #     print(f"     {line}")
            # print(f"     --- End of comparison for {repo_path} ---\n")
            # Write git commit changes to file: append this repo's full comparison output for later analysis (e.g. scan_ticket_formats.py)
            if self.commit_diff_log_path:
                try:
                    Path(self.commit_diff_log_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(self.commit_diff_log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n{'='*80}\n")
                        f.write(f"Repository: {repo_path} | Service: {service_name}\n")
                        f.write(f"Tags: {current_tag} → {new_tag}\n")
                        f.write(f"{'='*80}\n\n")
                        f.write(output)
                        f.write(f"\n{'='*80}\n\n")
                except OSError as e:
                    if self.verbose:
                        print(f"  ⚠️  Could not write commit diff log: {e}", file=sys.stderr)
        if not output:
            return {
                'service': service_name,
                'repository': repo_url,
                'repo_path': repo_path,
                'version_key': version_key,
                'current_tag': current_tag,
                'new_tag': new_tag,
                'status': 'error',
                'tickets': [],
                'ticket_count': 0,
                'error': 'Failed to fetch commits or no commits found'
            }
        
        # Extract tickets
        tickets = self.extract_tickets_from_text(output)
        sorted_tickets = sorted(tickets)
        
        if tickets:
            print(f"     ✅ Found {len(tickets)} tickets")
        else:
            print(f"     ℹ️  No tickets found")
        
        return {
            'service': service_name,
            'repository': repo_url,
            'repo_path': repo_path,
            'version_key': version_key,
            'current_tag': current_tag,
            'new_tag': new_tag,
            'status': 'success',
            'tickets': sorted_tickets,
            'ticket_count': len(tickets),
            'error': None
        }
    
    def process_all_services(self, services: List[Dict]) -> Dict:
        """
        Process all services and generate consolidated report.
        
        Args:
            services: List of service dictionaries from input.json
            
        Returns:
            Dictionary with all results and summary
        """
        print("=" * 70)
        print("Processing All Repositories")
        print("=" * 70)
        # Initialize file for logging git commit changes: create/truncate and write header (each repo's diff appended later in process_service)
        if self.commit_diff_log_path:
            try:
                Path(self.commit_diff_log_path).parent.mkdir(parents=True, exist_ok=True)
                with open(self.commit_diff_log_path, "w", encoding="utf-8") as f:
                    f.write("Commit differences with messages (all repositories)\n")
                    f.write(f"Generated at: {datetime.now().isoformat()}\n")
                    f.write("Use: python scan_ticket_formats.py <this_file> to find other Linear ticket formats.\n")
                    f.write("=" * 80 + "\n\n")
                if self.verbose:
                    print(f"📄 Writing commit diffs to: {self.commit_diff_log_path}\n")
            except OSError as e:
                if self.verbose:
                    print(f"  ⚠️  Could not create commit diff log: {e}", file=sys.stderr)
        results = []
        total_services = len(services)
        processed = 0
        skipped = 0
        failed = 0
        total_tickets = set()
        
        for i, service in enumerate(services, 1):
            print(f"\n[{i}/{total_services}] {service.get('service', 'unknown')}")
            
            if not self.should_process_service(service):
                print(f"  ⏭️  Skipping (no changes)")
                skipped += 1
                continue
            
            result = self.process_service(service)
            if result:
                results.append(result)
                if result['status'] == 'success':
                    processed += 1
                    total_tickets.update(result['tickets'])
                elif result['status'] == 'error':
                    failed += 1
            else:
                failed += 1
        
        # Generate summary
        print("\n" + "=" * 70)
        print("Summary")
        print("=" * 70)
        print(f"Total services: {total_services}")
        print(f"Processed: {processed}")
        print(f"Skipped (no changes): {skipped}")
        print(f"Failed: {failed}")
        print(f"Unique tickets across all services: {len(total_tickets)}")
        
        # Group tickets by project
        projects = {}
        for ticket in total_tickets:
            prefix = ticket.split('-')[0]
            if prefix not in projects:
                projects[prefix] = []
            projects[prefix].append(ticket)
        
        if projects:
            print(f"\nTickets by project:")
            for prefix in sorted(projects.keys()):
                print(f"  - {prefix}: {len(projects[prefix])} tickets")
        
        # Collect all unique tickets
        all_tickets_set = set()
        for result in results:
            if result["status"] == "success":
                for ticket in result["tickets"]:
                    all_tickets_set.add(ticket)
        
        # Fetch Linear details for all unique tickets
        ticket_details_map = self.fetch_all_ticket_details(all_tickets_set)

        # Omit tickets in Canceled / In Progress from aggregated outputs
        included_tickets_set = {
            tid for tid in all_tickets_set
            if not self._should_omit_ticket(ticket_details_map.get(tid))
        }
        
        # Collect unique project IDs from ticket details
        project_ids = set()
        for ticket_id in included_tickets_set:
            details = ticket_details_map.get(ticket_id)
            if details and details.get('projectId') and details['projectId'] != 'No Project':
                project_ids.add(details['projectId'])
        
        # Fetch project details
        project_details_map = self.fetch_all_project_details(project_ids)
        
        # Calculate max widths for uniform formatting
        max_ticket_id_len = max(len(tid) for tid in included_tickets_set) if included_tickets_set else 0
        max_status_len = 0
        for ticket_id in included_tickets_set:
            details = ticket_details_map.get(ticket_id)
            if details and details.get('state'):
                status_len = len(details['state'])
                max_status_len = max(max_status_len, status_len)
        # Ensure minimum width for "Unknown" status
        max_status_len = max(max_status_len, len('Unknown'))
        
        # Build all_tickets array as uniformly formatted strings: "TICKET-ID: status: Summary"
        # Also persist structured ticket_details (incl. priority) for release-ticket sorting.
        all_tickets = []
        ticket_details_out: Dict[str, Dict[str, object]] = {}
        for ticket_id in sorted(included_tickets_set):
            details = ticket_details_map.get(ticket_id)
            if details:
                try:
                    priority_value = int(details.get('priority', 0))
                except (TypeError, ValueError):
                    priority_value = 0
                entry: Dict[str, object] = {
                    'title': details.get('title') or '',
                    'state': details.get('state') or 'Unknown',
                    'priority': priority_value,
                }
                if details.get('issueUrl'):
                    entry['issueUrl'] = details['issueUrl']
                if details.get('fetchHttpStatus'):
                    entry['fetchHttpStatus'] = details['fetchHttpStatus']
                ticket_details_out[ticket_id] = entry

            if details and str(details.get('fetchHttpStatus')) == '400' and details.get('issueUrl'):
                all_tickets.append(f"{ticket_id}: {details['issueUrl']}")
            elif details and details.get('title'):
                # Format with uniform spacing: "AE-1234    : Done        : Ticket Summary"
                status = details.get('state', 'Unknown')
                formatted_ticket = f"{ticket_id:<{max_ticket_id_len}}: {status:<{max_status_len}}: {details['title']}"
                all_tickets.append(formatted_ticket)
            else:
                # If no details available, just include the ID
                all_tickets.append(ticket_id)
        
        # Group tickets by project prefix; sort each group by Linear priority.
        tickets_by_project = {}
        for ticket_id in sorted(included_tickets_set):
            prefix = ticket_id.split('-')[0]
            if prefix not in tickets_by_project:
                tickets_by_project[prefix] = []
            tickets_by_project[prefix].append(ticket_id)
        
        for prefix in tickets_by_project:
            tickets_by_project[prefix] = sorted(
                tickets_by_project[prefix],
                key=lambda tid: (
                    self._priority_sort_rank(
                        (ticket_details_map.get(tid) or {}).get('priority', 0)
                    ),
                    tid,
                ),
            )
        
        # Generate projects list for output
        projects_list = []
        for project_id in sorted(project_ids):
            project_details = project_details_map.get(project_id)
            if project_details:
                projects_list.append({
                    'id': project_details['id'],
                    'name': project_details['name'],
                    'url': project_details['url'],
                    'description': project_details['description'],
                    'state': project_details['state'],
                    'progress': project_details['progress']
                })
        
        # Generate output structure
        return {
            'metadata': {
                'generated_at': datetime.now().isoformat(),
                'total_services': total_services,
                'processed': processed,
                'skipped': skipped,
                'failed': failed,
                'total_unique_tickets': len(included_tickets_set),
                'total_unique_projects': len(projects_list)
            },
            'services': results,
            'all_tickets': all_tickets,
            'ticket_details': ticket_details_out,
            'tickets_by_project': tickets_by_project,
            'projects': projects_list
        }


def main():
    """Main function to handle command-line arguments and process repositories."""
    parser = argparse.ArgumentParser(
        description="Process all repositories from input.json and extract ticket changes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all repositories with changes
  python process_all_repos.py
  
  # Process all repositories including unchanged ones
  python process_all_repos.py --include-unchanged
  
  # Custom input and output files
  python process_all_repos.py --input custom_input.json --output results.json
  
  # Verbose output
  python process_all_repos.py --verbose
        """
    )
    
    parser.add_argument(
        "--input", "-i",
        default="generated_files/input_file/input.json",
        help="Input JSON file (default: generated_files/input_file/input.json)"
    )
    parser.add_argument(
        "--output", "-o",
        default="generated_files/final_tag_differences.json",
        help="Output JSON file (default: generated_files/final_tag_differences.json)"
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="Include services with no version changes"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty print JSON output"
    )
    parser.add_argument(
        "--no-fetch-details",
        action="store_true",
        help="Skip fetching ticket details from Linear API"
    )
    parser.add_argument(
        "--commit-diff-log",
        metavar="FILE",
        default="generated_files/commit_differences_with_messages.txt",
        help="Write commit diff with messages to FILE for format analysis (default: generated_files/commit_differences_with_messages.txt)"
    )
    parser.add_argument(
        "--no-commit-diff-log",
        action="store_true",
        help="Do not write commit diff log file"
    )
    
    args = parser.parse_args()
    
    # Read input file
    try:
        with open(args.input, 'r') as f:
            services = json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file '{args.input}' not found", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{args.input}': {e}", file=sys.stderr)
        sys.exit(1)
    
    if not isinstance(services, list):
        print("Error: Input JSON must be an array of service objects", file=sys.stderr)
        sys.exit(1)
    
    # Use output file from args
    output_file = args.output
    
    # Create output directory if it doesn't exist
    output_dir = Path(output_file).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if args.verbose:
        print(f"📝 Output file: {output_file}")
    
    # Initialize processor
    commit_diff_log_path = None if args.no_commit_diff_log else args.commit_diff_log
    processor = RepositoryProcessor(
        skip_unchanged=not args.include_unchanged,
        verbose=args.verbose,
        fetch_linear_details=not args.no_fetch_details if hasattr(args, 'no_fetch_details') else True,
        commit_diff_log_path=commit_diff_log_path
    )
    
    # Process all services
    results = processor.process_all_services(services)
    
    # Write output
    try:
        with open(output_file, 'w') as f:
            if args.pretty:
                json.dump(results, f, indent=2, ensure_ascii=False)
            else:
                json.dump(results, f, ensure_ascii=False)
        
        print(f"\n✅ Results saved to: {output_file}")
        
        # Generate separate projects_list.json file
        if 'projects' in results and results['projects']:
            projects_output_file = output_dir / "projects_list.json"
            with open(projects_output_file, 'w') as f:
                if args.pretty:
                    json.dump(results['projects'], f, indent=2, ensure_ascii=False)
                else:
                    json.dump(results['projects'], f, ensure_ascii=False)
            
            print(f"✅ Projects list saved to: {projects_output_file}")
            print(f"📊 Found {len(results['projects'])} unique projects")
        else:
            print("ℹ️  No projects found to save")
        
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

