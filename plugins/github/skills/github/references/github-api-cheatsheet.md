# The GitHub REST API through `gh api`

Reach for `gh api` only when no `gh` command covers the endpoint. It signs each call with `gh`'s
own login, so there is no token to deal with. Paths start after `https://api.github.com`, and
`{owner}` and `{repo}` come from the remote of the repository you are in.

## Repositories

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}` | One repository's details |
| POST | `/user/repos` | New repository for the user |
| POST | `/orgs/{org}/repos` | New repository in an organization |
| PATCH | `/repos/{owner}/{repo}` | Change a repository's settings |
| DELETE | `/repos/{owner}/{repo}` | Remove a repository |
| GET | `/user/repos?per_page=30&sort=updated` | The user's repositories, latest first |
| GET | `/orgs/{org}/repos` | An organization's repositories |
| POST | `/repos/{owner}/{repo}/forks` | Fork it |
| POST | `/repos/{owner}/{template}/generate` | New repository from a template |
| GET | `/repos/{owner}/{repo}/topics` | Read its topics |
| PUT | `/repos/{owner}/{repo}/topics` | Replace its topics |

## Pull requests

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/pulls?state=open` | Open pull requests |
| POST | `/repos/{owner}/{repo}/pulls` | Open a pull request |
| GET | `/repos/{owner}/{repo}/pulls/{number}` | One pull request |
| PATCH | `/repos/{owner}/{repo}/pulls/{number}` | Change a pull request |
| GET | `/repos/{owner}/{repo}/pulls/{number}/files` | Files a PR changes |
| PUT | `/repos/{owner}/{repo}/pulls/{number}/merge` | Merge it |
| POST | `/repos/{owner}/{repo}/pulls/{number}/requested_reviewers` | Ask for reviewers |
| POST | `/repos/{owner}/{repo}/pulls/{number}/reviews` | Post a review |
| POST | `/repos/{owner}/{repo}/pulls/{number}/comments` | Comment on a line |

### The body of a merge

```json
{"merge_method": "squash", "commit_title": "feat: description (#N)"}
```

`merge_method` takes `"merge"`, `"squash"` or `"rebase"`

### A review's `event`

`"APPROVE"`, `"REQUEST_CHANGES"`, `"COMMENT"`

## Issues

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/issues?state=open` | Open issues |
| POST | `/repos/{owner}/{repo}/issues` | Open an issue |
| GET | `/repos/{owner}/{repo}/issues/{number}` | One issue |
| PATCH | `/repos/{owner}/{repo}/issues/{number}` | Change an issue |
| POST | `/repos/{owner}/{repo}/issues/{number}/comments` | Comment on it |
| POST | `/repos/{owner}/{repo}/issues/{number}/labels` | Put labels on |
| DELETE | `/repos/{owner}/{repo}/issues/{number}/labels/{name}` | Take a label off |
| POST | `/repos/{owner}/{repo}/issues/{number}/assignees` | Assign people |
| GET | `/repos/{owner}/{repo}/labels` | The repository's labels |
| GET | `/search/issues?q={query}+repo:{owner}/{repo}` | Find issues |

The issues endpoints list pull requests too. Keep only the issues with
`--jq '.[] | select(.pull_request | not)'`.

## Actions and CI

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/actions/workflows` | The workflows |
| GET | `/repos/{owner}/{repo}/actions/runs?per_page=10` | Recent runs |
| GET | `/repos/{owner}/{repo}/actions/runs?branch={branch}` | Runs on one branch |
| GET | `/repos/{owner}/{repo}/actions/runs/{run_id}` | One run |
| GET | `/repos/{owner}/{repo}/actions/runs/{run_id}/logs` | A run's logs |
| POST | `/repos/{owner}/{repo}/actions/runs/{run_id}/rerun` | Run it again |
| POST | `/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs` | Run only the failed jobs again |
| POST | `/repos/{owner}/{repo}/actions/workflows/{id}/dispatches` | Start a workflow by hand |
| GET | `/repos/{owner}/{repo}/commits/{sha}/status` | Combined status of a commit |
| GET | `/repos/{owner}/{repo}/commits/{sha}/check-runs` | A commit's check runs |

## Releases

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/releases` | The releases |
| POST | `/repos/{owner}/{repo}/releases` | Publish a release |
| GET | `/repos/{owner}/{repo}/releases/{id}` | One release |
| DELETE | `/repos/{owner}/{repo}/releases/{id}` | Remove a release |

Files are attached to a release with `gh release upload <tag> <files>`.

## Secrets

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/actions/secrets` | The secrets' names |
| DELETE | `/repos/{owner}/{repo}/actions/secrets/{name}` | Remove a secret |

A secret is set with `gh secret set`, which encrypts it as GitHub requires.

## Protected branches

| Method | Path | What it does |
|---|---|---|
| GET | `/repos/{owner}/{repo}/branches/{branch}/protection` | Read the rules |
| PUT | `/repos/{owner}/{repo}/branches/{branch}/protection` | Replace the rules |
| DELETE | `/repos/{owner}/{repo}/branches/{branch}/protection` | Remove the rules |

## The signed-in user

| Method | Path | What it does |
|---|---|---|
| GET | `/user` | Who is signed in |
| GET | `/user/repos` | Their repositories |
| GET | `/gists` | Their gists |
| POST | `/gists` | New gist |
| GET | `/search/repositories?q={query}` | Find repositories |

## Pages

- `?per_page=100` (100 is the most), and `?page=2` for the page after
- Or `gh api --paginate <endpoint>`, which walks every page for you

## How many calls

- Signed in: 5,000 requests an hour
- What is left: `gh api rate_limit --jq .resources.core`

## The usual shapes of a `gh api` call

```bash
# Read, keeping one field with jq
gh api repos/{owner}/{repo} --jq .default_branch

# Create, sending fields (-f takes text, -F numbers and booleans)
gh api repos/{owner}/{repo}/issues --method POST -f title="..." -f body="..."

# Change
gh api repos/{owner}/{repo}/issues/42 --method PATCH -f state=closed

# Remove
gh api repos/{owner}/{repo}/issues/42/labels/bug --method DELETE

# The body read from a JSON file, for nested objects and arrays
gh api repos/{owner}/{repo}/pulls/42/reviews --method POST --input review.json

# A GraphQL query
gh api graphql -f query='query { viewer { login } }'
```

Any `gh api` call that writes changes GitHub, so Atlas's gate asks first: `--method` POST,
PUT, PATCH or DELETE, and also a call with a `-f`/`-F` field and no `--method`, since that is
sent as POST.
