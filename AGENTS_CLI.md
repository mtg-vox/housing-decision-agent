# Agent use of the housing CLI

Read [docs/cli.md](docs/cli.md) for syntax. The agent and dashboard operate on one user's private profile. There is no publishing workflow inside this app.

0. **No profile yet?** If `doctor` reports `profile_source: example`, follow the agent steps in [docs/SETUP.md](docs/SETUP.md): get questions with `housing --json init --questions`, ask the user, never invent answers, then `housing --json init --answers '{...}'`.
1. Start with `housing --json doctor`. Confirm the profile is the one the user intended; stop on a broken or ambiguous personal-profile path.
2. Use `--actor llm:<name>` for honest audit attribution. Actor labels are self-reported, not authentication or a sandbox. An agent with unrestricted shell/file access has the OS user's permissions.
3. Read before editing. Use returned versions/revisions for updates; on a conflict, refresh and reconcile instead of forcing an overwrite.
4. Record verified facts with sources and dates. Do not invent rents, availability, travel times, evidence, or neighborhood claims. Distinguish unknown values from zero or a reassuring default.
5. Explain judgments and cite existing evidence. Only genuine support should increase confidence.
6. Change housing preferences when instructed. Optional proposals let the user inspect a full change preview before applying; they are not an enforcement mechanism for distinguishing a human from an LLM.
7. Use the CLI/service for data edits, not hand-edits to files during concurrent dashboard use. Archive by default. Permanent deletion requires an explicit request and command confirmation.
8. Never publish the private profile, log, credentials, source snapshots, or research. Publication of application code is a separate user-authorized repository workflow.
9. Do not send unnecessary personal context to model providers or external research sites. State uncertainty and disclosure boundaries honestly.
