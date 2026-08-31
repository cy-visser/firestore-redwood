# Collaboration & Contribution Guide

This guide outlines the standard Git workflow and best practices for collaborating on the **Redwood Retail** project.

---

## 1. Branching & Git Workflow

Follow this step-by-step workflow when working on any bugfix, feature, or infrastructure modification.

### Step 1: Start from an Updated `main` Branch
Ensure your local `main` branch is in sync with the remote repository:
```bash
git checkout main
git pull origin main
```

### Step 2: Create and Switch to a Feature Branch
Create a new branch with a descriptive name prefixed by `feature/`, `fix/`, `chore/`, or `refactor/`:
```bash
git checkout -b feature/my-feature
```

### Step 3: Commit and Push the Branch
Stage your changes, author a descriptive commit message (following [Conventional Commits](https://www.conventionalcommits.org/)), and push to the remote repository:
```bash
git add .
git commit -m "feat: add initial feature module"
git push -u origin feature/my-feature
```

### Step 4: Open a Pull Request (PR)
1. Navigate to the repository in GitHub / GitLab.
2. Open a Pull Request from `feature/my-feature` into `main`.
3. Fill out the PR description outlining what changes were made, testing steps executed, and any Terraform resources added or modified.
4. Request reviews from team members before merging.

---

## 2. Pre-PR Verification Checklist

Before submitting your Pull Request, ensure that:

1. **Terraform Validation**:
   ```bash
   cd terraform/
   terraform fmt -check
   terraform validate
   ```
2. **No Sensitive Data**:
   Ensure `.tfstate`, `.env`, and credential files are not tracked (`git status` should show only intended source files).
3. **Python Lint & Testing**:
   Ensure all Python scripts run cleanly with Application Default Credentials (ADC) without hardcoded secrets.

---

## 3. Commit Message Standards

Use standard conventional commit prefixes:
* `feat:` A new feature or pipeline capability
* `fix:` A bug fix or pipeline stability correction
* `refactor:` Code refactoring that neither fixes a bug nor adds a feature
* `docs:` Documentation changes only (e.g., `README.md`, `COLLABORATION.md`)
* `chore:` Build process, tooling, or Terraform maintenance changes
