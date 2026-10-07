# Project workflow

- Finish application changes with tests, a Git commit, a push to the remote,
  and deployment to the existing production environment, unless the user asks otherwise.
- Preserve production resources, networking, secret references and `min_instances=1`
  when deploying. Never print credentials.
- Verify the deployed change and include a clickable verification URL in the final response.
- Public application domain: https://pacersreg.ru/ . Prefer a link directly to the changed page.
- Do not include unrelated generated presentations, screenshots or build outputs in commits.
