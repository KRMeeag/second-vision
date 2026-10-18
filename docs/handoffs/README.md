# Handoffs

Self-contained write-ups to give to another agent (for example one that revises the thesis paper). Each
subfolder holds its own handoff and the evidence it quotes, so it can be sent on its own, without the
repository.

| Folder | For | Contents | Date |
|---|---|---|---|
| [performance-tests/](performance-tests/HANDOFF.md) | A writing agent revising the paper's performance-testing text | Results of the first 20-minute performance run, how each of the paper's methods was actually carried out and why it changed, limits, what is still to measure, and the chart, screenshots and logs | 2026-10-18 |

Other handoffs, still kept where the work lives:
- the test-case revisions for the workbook: [../testing/WORKBOOK-REVISIONS.md](../testing/WORKBOOK-REVISIONS.md)
  and `../testing/workbook-revisions.csv` (for an agent editing the team's Google Sheet);
- the agent workflow: [../testing/HANDOFF.md](../testing/HANDOFF.md).

Rule for a new handoff: a folder named for the topic, a `HANDOFF.md` that states the task, the rules, the
facts and the limits, and an `evidence/` folder with copies of everything it quotes. A handoff is a
snapshot; the repository's `docs/testing/` files stay authoritative.
