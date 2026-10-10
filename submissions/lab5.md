# Task 1

1. **Link to your GitHub Actions run (green check)**
	https://github.com/Sentaru-01/SRE-Intro/actions/runs/28113038101
	
2. **Output of `gh api user/packages?package_type=container` showing pushed images**
	
	`nikita2@Ubuntu:~/SRE_course/SRE-Intro$ gh api user/packages?package_type=container`
`[`
  `{`
    `"id": 12996933,`
    `"name": "quickticket-gateway",`
    `"package_type": "container",`
    `"owner": {`
      `"login": "Sentaru-01",`
      `"id": 188181898,`
      `"node_id": "U_kgDOCzdtig",`
      `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
      `"gravatar_id": "",`
      `"url": "https://api.github.com/users/Sentaru-01",`
      `"html_url": "https://github.com/Sentaru-01",`
      `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
      `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
      `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
      `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
      `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
      `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
      `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
      `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
      `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
      `"type": "User",`
      `"user_view_type": "public",`
      `"site_admin": false`
    `},`
    `"visibility": "public",`
    `"url": "https://api.github.com/users/Sentaru-01/packages/container/quickticket-gateway",`
    `"created_at": "2026-06-24T16:19:36Z",`
    `"updated_at": "2026-06-24T17:21:37Z",`
    `"repository": {`
      `"id": 1263779505,`
      `"node_id": "R_kgDOS1O-sQ",`
      `"name": "SRE-Intro",`
      `"full_name": "Sentaru-01/SRE-Intro",`
      `"private": false,`
      `"owner": {`
        `"login": "Sentaru-01",`
        `"id": 188181898,`
        `"node_id": "U_kgDOCzdtig",`
        `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
        `"gravatar_id": "",`
        `"url": "https://api.github.com/users/Sentaru-01",`
        `"html_url": "https://github.com/Sentaru-01",`
        `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
        `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
        `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
        `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
        `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
        `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
        `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
        `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
        `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
        `"type": "User",`
        `"user_view_type": "public",`
        `"site_admin": false`
      `},`
      `"html_url": "https://github.com/Sentaru-01/SRE-Intro",`
      `"description": "🚀 SRE intro elective — 10 hands-on labs + 2 bonus operating QuickTicket microservices: SLO-driven observability (Prometheus/Grafana/Loki/Tempo), Kubernetes, GitOps with ArgoCD, alerting & incident response, progressive delivery with Argo Rollouts, chaos engineering, and DB reliability.",`
      `"fork": true,`
      `"url": "https://api.github.com/repos/Sentaru-01/SRE-Intro",`
      `"forks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/forks",`
      `"keys_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/keys{/key_id}",`
      `"collaborators_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/collaborators{/collaborator}",`
      `"teams_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/teams",`
      `"hooks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/hooks",`
      `"issue_events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/events{/number}",`
      `"events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/events",`
      `"assignees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/assignees{/user}",`
      `"branches_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/branches{/branch}",`
      `"tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/tags",`
      `"blobs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/blobs{/sha}",`
      `"git_tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/tags{/sha}",`
      `"git_refs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/refs{/sha}",`
      `"trees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/trees{/sha}",`
      `"statuses_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/statuses/{sha}",`
      `"languages_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/languages",`
      `"stargazers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/stargazers",`
      `"contributors_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contributors",`
      `"subscribers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscribers",`
      `"subscription_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscription",`
      `"commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/commits{/sha}",`
      `"git_commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/commits{/sha}",`
      `"comments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/comments{/number}",`
      `"issue_comment_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/comments{/number}",`
      `"contents_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contents/{+path}",`
      `"compare_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/compare/{base}...{head}",`
      `"merges_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/merges",`
      `"archive_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/{archive_format}{/ref}",`
      `"downloads_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/downloads",`
      `"issues_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues{/number}",`
      `"pulls_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/pulls{/number}",`
      `"milestones_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/milestones{/number}",`
      `"notifications_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/notifications{?since,all,participating}",`
      `"labels_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/labels{/name}",`
      `"releases_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/releases{/id}",`
      `"deployments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/deployments"`
    `},`
    `"html_url": "https://github.com/users/Sentaru-01/packages/container/package/quickticket-gateway"`
  `},`
  `{`
    `"id": 12996937,`
    `"name": "quickticket-events",`
    `"package_type": "container",`
    `"owner": {`
      `"login": "Sentaru-01",`
      `"id": 188181898,`
      `"node_id": "U_kgDOCzdtig",`
      `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
      `"gravatar_id": "",`
      `"url": "https://api.github.com/users/Sentaru-01",`
      `"html_url": "https://github.com/Sentaru-01",`
      `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
      `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
      `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
      `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
      `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
      `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
      `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
      `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
      `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
      `"type": "User",`
      `"user_view_type": "public",`
      `"site_admin": false`
    `},`
    `"visibility": "public",`
    `"url": "https://api.github.com/users/Sentaru-01/packages/container/quickticket-events",`
    `"created_at": "2026-06-24T16:19:48Z",`
    `"updated_at": "2026-06-24T17:21:48Z",`
    `"repository": {`
      `"id": 1263779505,`
      `"node_id": "R_kgDOS1O-sQ",`
      `"name": "SRE-Intro",`
      `"full_name": "Sentaru-01/SRE-Intro",`
      `"private": false,`
      `"owner": {`
        `"login": "Sentaru-01",`
        `"id": 188181898,`
        `"node_id": "U_kgDOCzdtig",`
        `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
        `"gravatar_id": "",`
        `"url": "https://api.github.com/users/Sentaru-01",`
        `"html_url": "https://github.com/Sentaru-01",`
        `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
        `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
        `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
        `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
        `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
        `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
        `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
        `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
        `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
        `"type": "User",`
        `"user_view_type": "public",`
        `"site_admin": false`
      `},`
      `"html_url": "https://github.com/Sentaru-01/SRE-Intro",`
      `"description": "🚀 SRE intro elective — 10 hands-on labs + 2 bonus operating QuickTicket microservices: SLO-driven observability (Prometheus/Grafana/Loki/Tempo), Kubernetes, GitOps with ArgoCD, alerting & incident response, progressive delivery with Argo Rollouts, chaos engineering, and DB reliability.",`
      `"fork": true,`
      `"url": "https://api.github.com/repos/Sentaru-01/SRE-Intro",`
      `"forks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/forks",`
      `"keys_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/keys{/key_id}",`
      `"collaborators_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/collaborators{/collaborator}",`
      `"teams_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/teams",`
      `"hooks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/hooks",`
      `"issue_events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/events{/number}",`
      `"events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/events",`
      `"assignees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/assignees{/user}",`
      `"branches_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/branches{/branch}",`
      `"tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/tags",`
      `"blobs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/blobs{/sha}",`
      `"git_tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/tags{/sha}",`
      `"git_refs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/refs{/sha}",`
      `"trees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/trees{/sha}",`
      `"statuses_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/statuses/{sha}",`
      `"languages_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/languages",`
      `"stargazers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/stargazers",`
      `"contributors_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contributors",`
      `"subscribers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscribers",`
      `"subscription_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscription",`
      `"commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/commits{/sha}",`
      `"git_commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/commits{/sha}",`
      `"comments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/comments{/number}",`
      `"issue_comment_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/comments{/number}",`
      `"contents_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contents/{+path}",`
      `"compare_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/compare/{base}...{head}",`
      `"merges_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/merges",`
      `"archive_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/{archive_format}{/ref}",`
      `"downloads_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/downloads",`
      `"issues_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues{/number}",`
      `"pulls_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/pulls{/number}",`
      `"milestones_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/milestones{/number}",`
      `"notifications_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/notifications{?since,all,participating}",`
      `"labels_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/labels{/name}",`
      `"releases_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/releases{/id}",`
      `"deployments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/deployments"`
    `},`
    `"html_url": "https://github.com/users/Sentaru-01/packages/container/package/quickticket-events"`
  `},`
  `{`
    `"id": 12996940,`
    `"name": "quickticket-payments",`
    `"package_type": "container",`
    `"owner": {`
      `"login": "Sentaru-01",`
      `"id": 188181898,`
      `"node_id": "U_kgDOCzdtig",`
      `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
      `"gravatar_id": "",`
      `"url": "https://api.github.com/users/Sentaru-01",`
      `"html_url": "https://github.com/Sentaru-01",`
      `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
      `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
      `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
      `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
      `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
      `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
      `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
      `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
      `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
      `"type": "User",`
      `"user_view_type": "public",`
      `"site_admin": false`
    `},`
    `"visibility": "public",`
    `"url": "https://api.github.com/users/Sentaru-01/packages/container/quickticket-payments",`
    `"created_at": "2026-06-24T16:19:58Z",`
    `"updated_at": "2026-06-24T17:21:58Z",`
    `"repository": {`
      `"id": 1263779505,`
      `"node_id": "R_kgDOS1O-sQ",`
      `"name": "SRE-Intro",`
      `"full_name": "Sentaru-01/SRE-Intro",`
      `"private": false,`
      `"owner": {`
        `"login": "Sentaru-01",`
        `"id": 188181898,`
        `"node_id": "U_kgDOCzdtig",`
        `"avatar_url": "https://avatars.githubusercontent.com/u/188181898?v=4",`
        `"gravatar_id": "",`
        `"url": "https://api.github.com/users/Sentaru-01",`
        `"html_url": "https://github.com/Sentaru-01",`
        `"followers_url": "https://api.github.com/users/Sentaru-01/followers",`
        `"following_url": "https://api.github.com/users/Sentaru-01/following{/other_user}",`
        `"gists_url": "https://api.github.com/users/Sentaru-01/gists{/gist_id}",`
        `"starred_url": "https://api.github.com/users/Sentaru-01/starred{/owner}{/repo}",`
        `"subscriptions_url": "https://api.github.com/users/Sentaru-01/subscriptions",`
        `"organizations_url": "https://api.github.com/users/Sentaru-01/orgs",`
        `"repos_url": "https://api.github.com/users/Sentaru-01/repos",`
        `"events_url": "https://api.github.com/users/Sentaru-01/events{/privacy}",`
        `"received_events_url": "https://api.github.com/users/Sentaru-01/received_events",`
        `"type": "User",`
        `"user_view_type": "public",`
        `"site_admin": false`
      `},`
      `"html_url": "https://github.com/Sentaru-01/SRE-Intro",`
      `"description": "🚀 SRE intro elective — 10 hands-on labs + 2 bonus operating QuickTicket microservices: SLO-driven observability (Prometheus/Grafana/Loki/Tempo), Kubernetes, GitOps with ArgoCD, alerting & incident response, progressive delivery with Argo Rollouts, chaos engineering, and DB reliability.",`
      `"fork": true,`
      `"url": "https://api.github.com/repos/Sentaru-01/SRE-Intro",`
      `"forks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/forks",`
      `"keys_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/keys{/key_id}",`
      `"collaborators_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/collaborators{/collaborator}",`
      `"teams_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/teams",`
      `"hooks_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/hooks",`
      `"issue_events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/events{/number}",`
      `"events_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/events",`
      `"assignees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/assignees{/user}",`
      `"branches_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/branches{/branch}",`
      `"tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/tags",`
      `"blobs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/blobs{/sha}",`
      `"git_tags_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/tags{/sha}",`
      `"git_refs_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/refs{/sha}",`
      `"trees_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/trees{/sha}",`
      `"statuses_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/statuses/{sha}",`
      `"languages_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/languages",`
      `"stargazers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/stargazers",`
      `"contributors_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contributors",`
      `"subscribers_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscribers",`
      `"subscription_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/subscription",`
      `"commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/commits{/sha}",`
      `"git_commits_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/git/commits{/sha}",`
      `"comments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/comments{/number}",`
      `"issue_comment_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues/comments{/number}",`
      `"contents_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/contents/{+path}",`
      `"compare_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/compare/{base}...{head}",`
      `"merges_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/merges",`
      `"archive_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/{archive_format}{/ref}",`
      `"downloads_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/downloads",`
      `"issues_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/issues{/number}",`
      `"pulls_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/pulls{/number}",`
      `"milestones_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/milestones{/number}",`
      `"notifications_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/notifications{?since,all,participating}",`
      `"labels_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/labels{/name}",`
      `"releases_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/releases{/id}",`
      `"deployments_url": "https://api.github.com/repos/Sentaru-01/SRE-Intro/deployments"`
    `},`
    `"html_url": "https://github.com/users/Sentaru-01/packages/container/package/quickticket-payments"`
  `}`
`]`
	
3. **Output of `argocd app get quickticket` showing Synced + Healthy**
	
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Name:               argocd/quickticket`
	`Project:            default`
	`Server:             https://kubernetes.default.svc`
	`Namespace:          default`
	`URL:                https://localhost:8443/applications/quickticket`
	`Source:`
	- `Repo:             https://github.com/sentaru-01/SRE-Intro.git`
	  `Target:`           
	  `Path:             k8s`
	`SyncWindow:         Sync Allowed`
	`Sync Policy:        Automated`
	`Sync Status:        Synced to  (4f699e0)`
	`Health Status:      Healthy`
	
	`GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE`
	       `Service     default    postgres  Synced  Healthy        service/postgres unchanged`
	       `Service     default    gateway   Synced  Healthy        service/gateway unchanged`
	       `Service     default    payments  Synced  Healthy        service/payments unchanged`
	       `Service     default    redis     Synced  Healthy        service/redis unchanged`
	       `Service     default    events    Synced  Healthy        service/events unchanged`
	`apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged`
	`apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged`
	`apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged`
	`apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged`
	`apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway unchanged`
	
4. **Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)**
	
	**kubectl get deployment gateway --show-labels**
	
	`NAME      READY   UP-TO-DATE   AVAILABLE   AGE     LABELS`
	`gateway   1/1     1            1           6d17h   app.kubernetes.io/managed-by=Helm,version=v2`
	
5. **Answer: "What happens if someone manually runs `kubectl edit` on a resource managed by ArgoCD?". **
	
	ArgoCD will immediately detect a **configuration drift** (mismatch with Git). Further behavior depends on the app's settings:
	1. **Automated Sync + Self-Heal ENABLED:** ArgoCD will **automatically overwrite** the manual changes almost instantly, reverting the resource to the state defined in Git.
	    
	2. **Automated Sync ENABLED, Self-Heal DISABLED:** The application status changes to **`OutOfSync`**. Manual edits remain active until a new commit is pushed or a manual sync is triggered.
	    
	3. **Manual Sync Mode:** The application transitions to **`OutOfSync`**. Changes persist in the cluster indefinitely until an administrator manually triggers a sync.


# Task 2

1.  **`argocd app get` showing Degraded after bad deploy**
	
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Name:               argocd/quickticket`
	`Project:            default`
	`Server:             https://kubernetes.default.svc`
	`Namespace:          default`
	`URL:                https://localhost:8443/applications/quickticket`
	`Source:`
	- `Repo:             https://github.com/sentaru-01/SRE-Intro.git`
	  `Target:`           
	  `Path:             k8s`
	`SyncWindow:         Sync Allowed`
	`Sync Policy:        Automated`
	`Sync Status:        Synced to  (e5ca83c)`
	`Health Status:      Progressing`
	
	`GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH       HOOK  MESSAGE`
	       `Service     default    payments  Synced  Healthy            service/payments unchanged`
	       `Service     default    gateway   Synced  Healthy            service/gateway unchanged`
	       `Service     default    events    Synced  Healthy            service/events unchanged`
	       `Service     default    postgres  Synced  Healthy            service/postgres unchanged`
	       `Service     default    redis     Synced  Healthy            service/redis unchanged`
	`apps   Deployment  default    events    Synced  Healthy            deployment.apps/events unchanged`
	`apps   Deployment  default    payments  Synced  Healthy            deployment.apps/payments unchanged`
	`apps   Deployment  default    redis     Synced  Healthy            deployment.apps/redis unchanged`
	`apps   Deployment  default    postgres  Synced  Healthy            deployment.apps/postgres unchanged`
	`apps   Deployment  default    gateway   Synced  Progressing        deployment.apps/gateway configured`
	
2.  **`kubectl get pods` showing ImagePullBackOff**
	
	`NAME                                                     READY   STATUS             RESTARTS       AGE`
	`alertmanager-monitoring-kube-prometheus-alertmanager-0   2/2     Running            0              6d16h`
	`events-54fbb6c756-4zdw7                                  1/1     Running            0              43m`
	`gateway-7b54578c86-dgbdd                                 0/1     ImagePullBackOff   0              2m12s`
	`gateway-85446d876f-dgzrg                                 1/1     Running            0              43m`
	`monitoring-grafana-65749cfb9-rczdg                       3/3     Running            0              6d16h`
	`monitoring-kube-prometheus-operator-7f87f6796-p5gkp      1/1     Running            0              6d16h`
	`monitoring-kube-state-metrics-b55bfdbfc-tlfkv            1/1     Running            0              6d16h`
	`monitoring-prometheus-node-exporter-ts4jn                1/1     Running            1 (140m ago)   6d17h`
	`payments-56b75d6888-glkbw                                1/1     Running            0              43m`
	`postgres-78489d7f5f-x7wv9                                1/1     Running            0              6d16h`
	`prometheus-monitoring-kube-prometheus-prometheus-0       2/2     Running            0              6d16h`
	`redis-6fcfb5475d-4qrms                                   1/1     Running` 
	
3. **`git log --oneline -3` showing the deploy + revert commits** 
	`35e9584 (HEAD -> main, origin/main, origin/HEAD) Revert "feat: deploy new gateway version"`
	`e5ca83c feat: deploy new gateway version`
	`4f699e0 feat: add version label to gateway`
	
4. **`argocd app get` showing Healthy after revert**
	
	`Handling connection for 8443`
	`Handling connection for 8443`
	`E0624 17:59:16.091058   26792 portforward.go:391] "Unhandled Error" err="error copying from remote stream to local connection: readfrom tcp6 [::1]:8443->[::1]:49982: write tcp6 [::1]:8443->[::1]:49982: write: broken pipe"`
	`Handling connection for 8443`
	`E0624 17:59:16.192538   26792 portforward.go:391] "Unhandled Error" err="error copying from remote stream to local connection: readfrom tcp6 [::1]:8443->[::1]:50002: write tcp6 [::1]:8443->[::1]:50002: write: broken pipe"`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Name:               argocd/quickticket`
	`Project:            default`
	`Server:             https://kubernetes.default.svc`
	`Namespace:          default`
	`URL:                https://localhost:8443/applications/quickticket`
	`Source:`
	- `Repo:             https://github.com/sentaru-01/SRE-Intro.git`
	  `Target:`           
	  `Path:             k8s`
	`SyncWindow:         Sync Allowed`
	`Sync Policy:        Automated`
	`Sync Status:        Synced to  (35e9584)`
	`Health Status:      Healthy`
	
	`GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE`
	       `Service     default    redis     Synced  Healthy        service/redis unchanged`
	       `Service     default    postgres  Synced  Healthy        service/postgres unchanged`
	       `Service     default    events    Synced  Healthy        service/events unchanged`
	       `Service     default    gateway   Synced  Healthy        service/gateway unchanged`
	       `Service     default    payments  Synced  Healthy        service/payments unchanged`
	`apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged`
	`apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured`
	`apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged`
	`apps   Deployment  default    events    Synced  Healthy        deployment.apps/events unchanged`
	`apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments unchanged`
	
5.  **Answer: "How long from `git revert` + push to pods being healthy again?"**
	The total time depends on how ArgoCD detects the new commit in the repository:
	
	- **With Manual Sync / Webhooks (Instant):** It takes about **10–20 seconds**. ArgoCD applies the reverted manifest immediately. Since the healthy image was running just a few minutes ago, it is already cached on the cluster nodes, allowing Kubernetes to recreate and pass readiness probes for the healthy pod almost instantly.
	    
	- **With Automated Sync (Default Polling):** It can take **up to 3 minutes**. By default, ArgoCD polls GitHub for changes every 180 seconds. Once the polling cycle detects the revert commit, the actual cluster reconciliation takes the same 10–20 seconds.


# Bonus Task

1.  **Updated workflow file showing auto-tag update**
	`name: CI`
	
	`on:`
	  `push:`
	    `branches: [main]`
	
	`jobs:`
	  `build:`
	    `runs-on: ubuntu-latest`
	    
	    if: "!startsWith(github.event.head_commit.message, 'ci:')"
	
	    permissions:
	      packages: write
	      contents: write
	
	    steps:
	      - uses: actions/checkout@v4
	
	      - name: Log in to GitHub Container Registry
	        uses: docker/login-action@v3
	        with:
	          registry: ghcr.io
	          username: ${{ github.actor }}
	          password: ${{ secrets.GITHUB_TOKEN }}
	
	      - name: Build and push gateway
	        run: |
	          USERNAME=$(echo "${{ github.actor }}" | tr '[:upper:]' '[:lower:]')
	          docker build -t ghcr.io/$USERNAME/quickticket-gateway:${{ github.sha }} ./app/gateway
	          docker push ghcr.io/$USERNAME/quickticket-gateway:${{ github.sha }}
	
	      - name: Build and push events
	        run: |
	          USERNAME=$(echo "${{ github.actor }}" | tr '[:upper:]' '[:lower:]')
	          docker build -t ghcr.io/$USERNAME/quickticket-events:${{ github.sha }} ./app/events
	          docker push ghcr.io/$USERNAME/quickticket-events:${{ github.sha }}
	
	      - name: Build and push payments
	        run: |
	          USERNAME=$(echo "${{ github.actor }}" | tr '[:upper:]' '[:lower:]')
	          docker build -t ghcr.io/$USERNAME/quickticket-payments:${{ github.sha }} ./app/payments
	          docker push ghcr.io/$USERNAME/quickticket-payments:${{ github.sha }}
	
	      - name: Update image tags in manifests
	        run: |
	          USERNAME=$(echo "${{ github.actor }}" | tr '[:upper:]' '[:lower:]')
	          SHA=${{ github.sha }}
	          sed -i "s|image: ghcr.io/.*/quickticket-gateway:.*|image: ghcr.io/${USERNAME}/quickticket-gateway:${SHA}|" k8s/gateway.yaml
	          sed -i "s|image: ghcr.io/.*/quickticket-events:.*|image: ghcr.io/${USERNAME}/quickticket-events:${SHA}|" k8s/events.yaml
	          sed -i "s|image: ghcr.io/.*/quickticket-payments:.*|image: ghcr.io/${USERNAME}/quickticket-payments:${SHA}|" k8s/payments.yaml
	
	      - name: Commit and push manifest update
	        run: |
	          git config user.name "github-actions"
	          git config user.email "github-actions@github.com"
	          git add k8s/
	          git diff --cached --quiet || git commit -m "ci: update image tags to ${{ github.sha }}"
	          git push

2.  **Git log showing: code commit → CI tag-update commit**
	`c954ee0 (HEAD -> main, origin/main, origin/HEAD) ci: update image tags to 7ca26bd76ac3f11f03b9e2fd872d9ae1220c830e`
	`7ca26bd chore: trigger automated gitops loop`
	`b16a2b2 chore: trigger automated gitops loop`

3.  **Git log showing: code commit → CI tag-update commit**
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Handling connection for 8443`
	`Name:               argocd/quickticket`
	`Project:            default`
	`Server:             https://kubernetes.default.svc`
	`Namespace:          default`
	`URL:                https://localhost:8443/applications/quickticket`
	`Source:`
	`￼- Repo:             https://github.com/sentaru-01/SRE-Intro.git`
	  `Target:`           
	  `Path:             k8s`
	`SyncWindow:         Sync Allowed`
	`Sync Policy:        Automated`
	`Sync Status:        Synced to  (c954ee0)`
	`Health Status:      Healthy`
	
	`￼GROUP  KIND        NAMESPACE  NAME      STATUS  HEALTH   HOOK  MESSAGE`
	       `Service     default    gateway   Synced  Healthy        service/gateway unchanged`
	       `Service     default    events    Synced  Healthy        service/events unchanged`
	       `Service     default    payments  Synced  Healthy        service/payments unchanged`
	       `Service     default    postgres  Synced  Healthy        service/postgres unchanged`
	       `Service     default    redis     Synced  Healthy        service/redis unchanged`
	`apps   Deployment  default    redis     Synced  Healthy        deployment.apps/redis unchanged`
	`apps   Deployment  default    events    Synced  Healthy        deployment.apps/events configured`
	`apps   Deployment  default    gateway   Synced  Healthy        deployment.apps/gateway configured`
	`apps   Deployment  default    postgres  Synced  Healthy        deployment.apps/postgres unchanged`
	`apps   Deployment  default    payments  Synced  Healthy        deployment.apps/payments configured`
