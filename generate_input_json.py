#!/usr/bin/env python3
"""
Generate Input JSON for Tag Comparison

This script creates a mapping of services to their version keys, fetches current deployed
versions from version.json and new versions from the GitHub .env file, then generates
an input.json file with current and new tag combinations.

Usage:
    python generate_input_json.py
    python generate_input_json.py --version-url https://cloud.stackgen.com/version.json
    python generate_input_json.py --env-url https://raw.githubusercontent.com/appcd-dev/appcd-dist/main/.env
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import ssl
from typing import Dict, Optional, Tuple
import re
import requests


# Service to version key and repository mapping
SERVICE_VERSION_MAP = {
    "infraops": {
        "version_key": "INFRAOPS",
        "repository": "https://github.com/appcd-dev/infraops"
    },
    "backstage-adapter": {
        "version_key": "BACKSTAGE_ADAPTER_VERSION",
        "repository": "https://github.com/appcd-dev/backstage-adapter"
    },
    "sgai-orchestration": {
        "version_key": "SGAI_ORCHESTRATION",
        "repository": "https://github.com/appcd-dev/sgai-orchestration"
    },
    "aiden": {
        "version_key": "AIDEN_VERSION",
        "repository": "https://github.com/appcd-dev/aiden"
    },
    "aiden-ui": {
        "version_key": "AIDEN_UI_VERSION",
        "repository": "https://github.com/appcd-dev/aiden-ui-v2"
    },
    "stackgen-guild": {
        "version_key": "STACKGEN_GUILD",
        "repository": "https://github.com/appcd-dev/stackgen-guild"
    },
    "stackgen-sre-app": {
        "version_key": "STACKGEN_SRE_APP",
        "repository": "https://github.com/appcd-dev/stackgen-sre-app"
    }
}


def get_latest_tag_from_github(repo: str, token: Optional[str] = None) -> Optional[str]:
    """
    Fetch the latest tag from a GitHub repository.
    
    Args:
        repo: Repository in format 'owner/repo'
        token: GitHub Personal Access Token. If None, will try to get from env variables.
        
    Returns:
        Latest tag name (without 'refs/tags/' prefix) or None if failed
    """
    # Try to get token from environment if not provided
    if not token:
        token = os.getenv('GITHUB_PAT') or os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    
    headers = {
        'Accept': 'application/vnd.github.v3+json'
    }
    
    if token:
        headers['Authorization'] = f'token {token}'
    
    # GitHub API endpoint to list tags
    url = f"https://api.github.com/repos/{repo}/tags"
    
    try:
        response = requests.get(url, headers=headers, timeout=30, params={'per_page': 1})
        response.raise_for_status()
        
        tags = response.json()
        if tags and len(tags) > 0:
            # Return the tag name (without 'refs/tags/' prefix if present)
            tag_name = tags[0].get('name', '')
            return tag_name if tag_name else None
        
        return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️  Warning: Failed to fetch latest tag from {repo}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  Warning: Error fetching latest tag from {repo}: {e}", file=sys.stderr)
        return None


def fetch_env_file_from_github_tag(repo: str, tag: str, file_path: str = ".env", token: Optional[str] = None) -> Optional[str]:
    """
    Fetch the .env file content from a GitHub repository at a specific tag.
    
    Args:
        repo: Repository in format 'owner/repo'
        tag: Tag name (e.g., 'v2025.12.23')
        file_path: Path to the file (default: ".env")
        token: GitHub Personal Access Token. If None, will try to get from env variables.
        
    Returns:
        File content as string if found, or None if not found
    """
    # Try to get token from environment if not provided
    if not token:
        token = os.getenv('GITHUB_PAT') or os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    
    headers = {
        'Accept': 'application/vnd.github.v3+json'
    }
    
    if token:
        headers['Authorization'] = f'token {token}'
    
    # Fetch file from specific tag
    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    params = {'ref': tag}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            # GitHub API returns file content in base64 encoding
            if data.get('encoding') == 'base64':
                import base64
                content = base64.b64decode(data['content']).decode('utf-8')
                return content
            else:
                # If not base64, try to get content directly
                content = data.get('content', '')
                if content:
                    import base64
                    try:
                        content = base64.b64decode(content).decode('utf-8')
                        return content
                    except:
                        return content
        return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️  Warning: Failed to fetch {file_path} from {repo} at tag {tag}: {e}", file=sys.stderr)
        return None


def fetch_env_file_from_github_repo(repo: str, file_path: str = ".env", token: Optional[str] = None, preferred_branch: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Fetch the .env file content from a GitHub repository by trying different branches.
    
    Args:
        repo: Repository in format 'owner/repo'
        file_path: Path to the file (default: ".env")
        token: GitHub Personal Access Token. If None, will try to get from env variables.
        preferred_branch: Branch to try first (e.g., from URL)
        
    Returns:
        Tuple of (branch_name, file_content) if found, or None if not found
    """
    # Try to get token from environment if not provided
    if not token:
        token = os.getenv('GITHUB_PAT') or os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    
    headers = {
        'Accept': 'application/vnd.github.v3+json'
    }
    
    if token:
        headers['Authorization'] = f'token {token}'
    
    # Try preferred branch first, then common branch names
    branches_to_try = []
    if preferred_branch:
        branches_to_try.append(preferred_branch)
    branches_to_try.extend(['main', 'master', 'develop', 'dev'])
    # Remove duplicates while preserving order
    seen = set()
    branches_to_try = [b for b in branches_to_try if not (b in seen or seen.add(b))]
    
    for branch in branches_to_try:
        url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
        params = {'ref': branch}
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                # GitHub API returns file content in base64 encoding
                if data.get('encoding') == 'base64':
                    import base64
                    content = base64.b64decode(data['content']).decode('utf-8')
                    return (branch, content)
                else:
                    # If not base64, try to get content directly
                    content = data.get('content', '')
                    if content:
                        import base64
                        try:
                            content = base64.b64decode(content).decode('utf-8')
                            return (branch, content)
                        except:
                            return (branch, content)
        except requests.exceptions.RequestException:
            continue
    
    return None


def convert_github_blob_to_raw_url(url: str) -> str:
    """
    Convert GitHub blob URL to raw URL.
    
    Converts URLs like:
    https://github.com/owner/repo/blob/branch/path/to/file
    to:
    https://raw.githubusercontent.com/owner/repo/branch/path/to/file
    
    Args:
        url: GitHub blob URL or any URL
        
    Returns:
        Converted raw URL if it's a GitHub blob URL, otherwise returns the original URL
    """
    # Pattern: https://github.com/{owner}/{repo}/blob/{branch}/{path}
    github_blob_pattern = r'^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$'
    
    match = re.match(github_blob_pattern, url)
    if match:
        owner, repo, rest = match.groups()
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
        return raw_url
    
    # Return original URL if it doesn't match the pattern
    return url


def fetch_url_content(url: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Fetch content from a URL.
    
    Args:
        url: URL to fetch content from
        timeout: Request timeout in seconds
        
    Returns:
        Tuple of (success, content)
    """
    try:
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        # Create SSL context that doesn't verify certificates
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            content = response.read().decode('utf-8')
            return True, content
                
    except urllib.error.HTTPError as e:
        return False, f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URL Error: {e.reason}"
    except Exception as e:
        return False, f"Error: {str(e)}"


def parse_version_json(content: str) -> Optional[Dict[str, str]]:
    """
    Parse version.json content.
    
    Args:
        content: Raw JSON content
        
    Returns:
        Dictionary of version keys and values
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse version.json: {e}", file=sys.stderr)
        return None


def parse_env_file(content: str) -> Dict[str, str]:
    """
    Parse .env file content.
    
    Args:
        content: Raw .env file content
        
    Returns:
        Dictionary of environment variables
    """
    env_vars = {}
    for line in content.split('\n'):
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith('#'):
            continue
        
        # Parse KEY=VALUE format
        match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$', line)
        if match:
            key, value = match.groups()
            # Remove quotes if present
            value = value.strip().strip('"').strip("'")
            env_vars[key] = value
    
    return env_vars


def generate_input_json(current_versions: Dict[str, str], new_versions: Dict[str, str]) -> list:
    """
    Generate input JSON structure with current and new tag combinations.
    
    Args:
        current_versions: Dictionary of currently deployed versions (from version.json)
                         Keys are service names (e.g., "ui", "appcd")
        new_versions: Dictionary of new versions from .env file
                     Keys are environment variable names (e.g., "APPCDUI_VERSION")
        
    Returns:
        List of dictionaries with service, repository, version_key, current_tag, and new_tag
    """
    result = []
    
    for service_name, service_info in SERVICE_VERSION_MAP.items():
        version_key = service_info["version_key"]
        repository = service_info["repository"]
        
        # Current versions use service names as keys (from version.json)
        # Default to v1.0.0 if version not found
        current_tag = current_versions.get(service_name, "v1.0.0")
        # New versions use environment variable names as keys (from .env)
        new_tag = new_versions.get(version_key, "")
        
        # Only include if at least one version exists
        if current_tag or new_tag:
            result.append({
                "service": service_name,
                "repository": repository,
                "version_key": version_key,
                "current_tag": current_tag,
                "new_tag": new_tag
            })
    
    return result


def read_local_env_file(file_path: str) -> Optional[Dict[str, str]]:
    """
    Read and parse a local .env file.
    
    Args:
        file_path: Path to the .env file
        
    Returns:
        Dictionary of environment variables or None if failed
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return parse_env_file(content)
    except Exception as e:
        print(f"❌ Failed to read local .env file: {e}", file=sys.stderr)
        return None


def main():
    """Main function to handle command-line arguments and generate input.json."""
    parser = argparse.ArgumentParser(
        description="Generate input.json with current and new version tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate with default production version.json (override --version-url if needed)
  python generate_input_json.py --env-url ...
  
  # Non-production deployed versions
  python generate_input_json.py --version-url https://stage.dev.stackgen.com/version.json --env-url ...
  
  # Use local .env file
  python generate_input_json.py --env-file .env
  
  # Custom output file
  python generate_input_json.py --output my_input.json
        """
    )
    
    parser.add_argument(
        "--version-url",
        default="https://cloud.stackgen.com/version.json",
        help="URL for deployed version.json (default: production cloud.stackgen.com)",
    )
    parser.add_argument(
        "--env-url",
        help="URL to fetch new .env file from (e.g., GitHub raw URL)"
    )
    parser.add_argument(
        "--env-file",
        help="Path to local .env file to use instead of URL"
    )
    parser.add_argument(
        "--output", "-o",
        default="generated_files/input_file/input.json",
        help="Output file path (default: generated_files/input_file/input.json)"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty print JSON output"
    )
    parser.add_argument(
        "--stackgen-tag",
        metavar="TAG",
        help="Tag in appcd-dev/appcd-dist to fetch .env from (e.g. v2026.2.7). When set with appcd-dist env URL, fetches .env via GitHub API at this tag. Required when using make generate-input-custom."
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.env_url and not args.env_file:
        print("❌ Error: Either --env-url or --env-file must be provided", file=sys.stderr)
        print("\nExamples:")
        print("  python generate_input_json.py --env-file .env")
        print("  python generate_input_json.py --env-url https://raw.githubusercontent.com/.../main/.env")
        sys.exit(1)
    
    if args.env_url and args.env_file:
        print("⚠️  Warning: Both --env-url and --env-file provided. Using --env-file", file=sys.stderr)
    
    print("=" * 70)
    print("Generating Input JSON for Tag Comparison")
    print("=" * 70)
    
    # Fetch current versions from version.json
    print(f"\n📥 Fetching current versions from: {args.version_url}")
    success, content = fetch_url_content(args.version_url, args.timeout)
    
    if not success:
        print(f"❌ Failed to fetch version.json: {content}", file=sys.stderr)
        sys.exit(1)
    
    current_versions = parse_version_json(content)
    if current_versions is None:
        sys.exit(1)
    
    print(f"✅ Successfully fetched current versions ({len(current_versions)} keys)")
    
    # Fetch new versions from .env file (local or URL)
    new_versions = None
    new_tags_source = ""

    if args.env_file:
        print(f"\n📥 Reading new versions from local file: {args.env_file}")
        new_versions = read_local_env_file(args.env_file)
        new_tags_source = args.env_file
    else:
        # Convert GitHub blob URL to raw URL if needed
        env_url = convert_github_blob_to_raw_url(args.env_url)
        if env_url != args.env_url:
            print(f"\n🔄 Converted GitHub blob URL to raw URL:")
            print(f"   Original: {args.env_url}")
            print(f"   Raw URL:  {env_url}")
        
        print(f"\n📥 Fetching new versions from: {env_url}")
        success, content = fetch_url_content(env_url, args.timeout)
        
        if not success:
            # If it's a 404 and a GitHub URL, try to find the file using GitHub API
            if "404" in content or "Not Found" in content:
                # Remove query parameters and extract repo info from URL
                # Handle URLs like: https://raw.githubusercontent.com/owner/repo/refs/heads/branch/file?token=...
                url_without_query = env_url.split('?')[0]  # Remove query parameters
                
                # Extract repo info from URL - try both converted and original URLs
                # Pattern handles: raw.githubusercontent.com/owner/repo/branch/file or refs/heads/branch/file
                raw_github_pattern = r'https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:refs/heads/)?([^/]+)/(.+)'
                blob_github_pattern = r'https://github\.com/([^/]+)/([^/]+)/blob/(?:refs/heads/)?([^/]+)/(.+)'
                
                # Try to match the converted URL first, then the original
                match = re.match(raw_github_pattern, url_without_query)
                if not match:
                    original_url_no_query = args.env_url.split('?')[0]
                    match = re.match(blob_github_pattern, original_url_no_query)
                
                if match:
                    owner = match.group(1)
                    repo = match.group(2)
                    branch = match.group(3)
                    # For raw URL: group 4 is the file path
                    # For blob URL: group 4 is the file path
                    file_path = match.group(4)
                    
                    print(f"\n🔍 File not found at specified URL. Searching for {file_path} in {owner}/{repo}...")
                    github_token = os.getenv('GITHUB_PAT') or os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
                    
                    # Try the specific branch first, then fall back to common branches
                    result = fetch_env_file_from_github_repo(f"{owner}/{repo}", file_path, github_token, preferred_branch=branch)
                    
                    if result:
                        found_branch, file_content = result
                        print(f"✅ Found file in branch: {found_branch}")
                        print(f"📥 Fetching content using GitHub API...")
                        content = file_content
                        success = True
                        env_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{found_branch}/{file_path} (fetched via API)"
                    else:
                        print(f"❌ Could not find {file_path} in {owner}/{repo} repository.", file=sys.stderr)
                        print(f"   Tried branches: {branch}, main, master, develop, dev", file=sys.stderr)
                        if not github_token:
                            print(f"   Note: GitHub token may be required. Set GITHUB_PAT or GH_TOKEN environment variable.", file=sys.stderr)
                        print(f"   Original error: {content}", file=sys.stderr)
                        sys.exit(1)
                else:
                    print(f"❌ Failed to fetch .env file: {content}", file=sys.stderr)
                    sys.exit(1)
            else:
                print(f"❌ Failed to fetch .env file: {content}", file=sys.stderr)
                sys.exit(1)
        
        new_versions = parse_env_file(content)
        new_tags_source = env_url

    if new_versions is None:
        sys.exit(1)
    
    print(f"✅ Successfully fetched new versions ({len(new_versions)} keys)")
    
    # Generate input JSON
    print(f"\n🔄 Generating input JSON...")
    input_data = generate_input_json(current_versions, new_versions)
    
    # Update appcd current_tag only when --stackgen-tag is provided: fetch .env from appcd-dist at that tag
    # (Do not use appcd's current_tag from version.json—e.g. v0.75.1-hotfix.1—since that tag may not exist in appcd-dist.)
    if args.stackgen_tag:
        print(f"\n🔍 Updating appcd current_tag using stackgen_tag: {args.stackgen_tag}...")
        github_token = os.getenv('GITHUB_PAT') or os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
        appcd_updated = False
        for item in input_data:
            if item.get("service") == "appcd":
                print(f"   Fetching .env file from appcd-dist at tag {args.stackgen_tag}...")
                env_content = fetch_env_file_from_github_tag("appcd-dev/appcd-dist", args.stackgen_tag, ".env", github_token)
                if env_content:
                    tag_env_vars = parse_env_file(env_content)
                    appcd_version_from_tag = tag_env_vars.get("APPCD_VERSION", "")
                    if appcd_version_from_tag:
                        item["current_tag"] = appcd_version_from_tag
                        print(f"✅ Updated appcd current_tag:")
                        print(f"   stackgen_tag: {args.stackgen_tag} (appcd-dist)")
                        print(f"   appcd current_tag: {appcd_version_from_tag} (APPCD_VERSION from appcd-dist/{args.stackgen_tag}/.env)")
                    else:
                        print(f"⚠️  Warning: APPCD_VERSION not found in .env from appcd-dist tag {args.stackgen_tag}. Keeping current_tag: {item.get('current_tag', '')}")
                else:
                    print(f"⚠️  Warning: Could not fetch .env from appcd-dist at tag {args.stackgen_tag}. Keeping current_tag: {item.get('current_tag', '')}", file=sys.stderr)
                    if not github_token:
                        print(f"   Tip: Set GITHUB_PAT or GH_TOKEN for tag fetching.", file=sys.stderr)
                appcd_updated = True
                break
        if not appcd_updated:
            print("⚠️  Warning: appcd service not found in input data. Skipping tag update.")
    else:
        print(f"\nℹ️  No --stackgen-tag provided. Skipping appcd current_tag update (use make generate-input-custom STACKGEN_TAG=<tag>).")
    
    # Write to file
    output_path = args.output
    
    # Create directory if it doesn't exist
    from pathlib import Path
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(output_path, 'w') as f:
            if args.pretty:
                json.dump(input_data, f, indent=2, ensure_ascii=False)
            else:
                json.dump(input_data, f, ensure_ascii=False)
        
        print(f"✅ Successfully wrote {len(input_data)} service mappings to: {output_path}")
        
        # Display summary (sources match how current_tag / new_tag were loaded above)
        summary_header = "Summary"
        summary_sources = (
            f"  Current tags ← deployed version.json: {args.version_url}\n"
            f"  New tags ← candidate .env:            {new_tags_source}"
        )
        if args.stackgen_tag:
            summary_sources += (
                f"\n  appcd current_tag: overridden from appcd-dev/appcd-dist "
                f"tag {args.stackgen_tag} (.env APPCD_VERSION) when applicable"
            )
        print("\n" + "=" * 70)
        print(summary_header)
        print(summary_sources)
        print("=" * 70)
        print(f"{'Service':<25} {'Current tag':<22} →  {'New tag'}")
        print("-" * 70)
        for item in input_data:
            status = "🔄" if item["current_tag"] != item["new_tag"] else "✓"
            print(f"{status} {item['service']:25} {item['current_tag']:20} → {item['new_tag']}")
        
        print("\n" + "=" * 70)
        print(f"✅ Input JSON generated successfully: {output_path}")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ Failed to write output file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

