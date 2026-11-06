# Looking after repositories

Cloning, creating, forking and configuring GitHub repositories, and keeping them in shape: with
`gh`, or with `git` alone when that is all it takes.

## Before you start

- `gh auth status` passes (see `references/auth.md`)

When a command needs the user's GitHub login, get it with `gh api user --jq .login`. Inside
`gh api` paths, `{owner}` and `{repo}` come from the remote of the repository you are in.

---

## 1. Cloning

```bash
gh repo clone owner/repo-name
gh repo clone owner/repo-name -- --depth 1

# git alone also clones; over HTTPS it signs in with gh once `gh auth setup-git` has run
git clone https://github.com/owner/repo-name.git
git clone https://github.com/owner/repo-name.git ./my-local-dir
git clone --depth 1 https://github.com/owner/repo-name.git
git clone --branch develop https://github.com/owner/repo-name.git

# Over SSH, when the machine has an SSH key set up
git clone git@github.com:owner/repo-name.git
```

## 2. A new repository

```bash
# Public, and cloned right away
gh repo create my-new-project --public --clone

# Private, with a description and a license
gh repo create my-new-project --private --description "Small CLI for tidying notes" --license MIT --clone

# Owned by an organization rather than the user
gh repo create my-org/my-new-project --public --clone

# From a folder that already exists here (run it from inside that folder)
gh repo create my-project --source . --public --push
```

### Starting from a template

```bash
gh repo create my-new-app --template owner/template-repo --public --clone
```

## 3. Forks

```bash
gh repo fork owner/repo-name --clone
```

With `--clone`, the repository you forked from is also added as the `upstream` remote.

### Bringing a fork up to date

```bash
gh repo sync <your-user>/repo-name

# The same with git alone
git fetch upstream
git checkout main
git merge upstream/main
git push origin main
```

## 4. What a repository is

```bash
gh repo view owner/repo-name
gh repo view owner/repo-name --json nameWithOwner,description,stargazerCount,forkCount,defaultBranchRef,primaryLanguage
gh repo list --limit 20
gh search repos "machine learning" --language python --sort stars
```

## 5. Settings

```bash
gh repo edit --description "New description" --visibility public
gh repo edit --enable-wiki=false --enable-issues=true
gh repo edit --default-branch main
gh repo edit --add-topic "machine-learning,python"
gh repo edit --enable-auto-merge
```

A change of visibility wants `--accept-visibility-change-consequences`. Before you run it, tell
the user in plain words what making the repository public, or private, will do.

## 6. Protecting a branch

No `gh` command does this; go through the API.

```bash
# The rules in force today
gh api repos/{owner}/{repo}/branches/main/protection
```

Setting it takes the rules in a JSON file, written with `write_file`:

```json
{
  "required_status_checks": {"strict": true, "contexts": ["ci/test", "ci/lint"]},
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null
}
```

```bash
gh api repos/{owner}/{repo}/branches/main/protection --method PUT --input protection.json
```

## 7. Secrets for GitHub Actions

```bash
gh secret set API_KEY              # gh asks for the value and the user enters it
gh secret set SSH_KEY < ~/.ssh/id_rsa
gh secret list
gh secret delete API_KEY
```

A secret's value never appears in a command line you write. Either `gh secret set` asks the
user for it, or it is read from a file the user names.

## 8. Releases

```bash
gh release create v1.0.0 --title "v1.0.0" --generate-notes
gh release create v2.0.0-rc1 --draft --prerelease --generate-notes
gh release create v1.0.0 ./dist/binary --title "v1.0.0" --notes "What changed in this version"
gh release upload v1.0.0 ./dist/binary-amd64
gh release list
gh release download v1.0.0 --dir ./downloads
```

Once it is created, check it: `gh release view v1.0.0 --json tagName,isDraft,publishedAt,assets`.

## 9. Workflows and runs

```bash
gh workflow list
gh run list --limit 10
gh run view <RUN_ID>
gh run view <RUN_ID> --log-failed
gh run view <RUN_ID> --log          # every line, not just the failures
gh run rerun <RUN_ID>
gh run rerun <RUN_ID> --failed
gh workflow run ci.yml --ref main
gh workflow run deploy.yml -f environment=staging
```

A long log is better kept in a file: send it to a temporary folder (`$(mktemp -d)` in bash,
`$env:TEMP` in PowerShell), then read it with `read_file` or search it with `find_files`.

## 10. Gists

```bash
gh gist create script.py --public --desc "Helper script"
gh gist list
```

## At a glance

| To | gh |
|--------|-----|
| Clone a repository | `gh repo clone o/r` |
| Make a new one | `gh repo create name --public` |
| Fork one | `gh repo fork o/r --clone` |
| Read what it is | `gh repo view o/r` |
| Change its settings | `gh repo edit --...` |
| Protect a branch | `gh api repos/{owner}/{repo}/branches/main/protection` |
| Publish a release | `gh release create v1.0` |
| See the workflows | `gh workflow list` |
| Run CI again | `gh run rerun ID` |
| Store a secret | `gh secret set KEY` |
