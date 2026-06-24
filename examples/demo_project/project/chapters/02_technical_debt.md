# Technical Debt

Every codebase accumulates technical debt. The question is not whether your
team will carry debt, but whether it will carry a manageable amount at a
manageable interest rate.

The term "technical debt" was coined by Ward Cunningham to describe the cost
of expedient decisions — choices that solved an immediate problem but made the
codebase harder to change. Like financial debt, technical debt is not
inherently bad. Taking on debt to ship a product faster is sometimes the right
trade. The problem arises when the interest payments — the extra time required
to work around past shortcuts — become large enough to crowd out new work.

## A Simple Taxonomy

Not all technical debt is the same. It helps to distinguish three types:

1. **Deliberate debt.** A known shortcut taken consciously to meet a deadline,
   with an explicit plan to pay it back. This is the only kind of debt that is
   clearly worth taking on.
2. **Inadvertent debt.** Code that seemed clean at the time but is now
   understood to be a problem. This is normal and inevitable as requirements
   evolve.
3. **Bit rot.** Dependencies, patterns, and assumptions that have aged out.
   The code was never wrong; it has simply become inconsistent with the rest
   of the system over time.

Each type calls for a different response. Deliberate debt should be tracked and
scheduled. Inadvertent debt should be addressed during normal feature work when
the cost to fix is low. Bit rot should be handled through regular investment
cycles — dedicated time to upgrade dependencies and modernise patterns.

## Measuring Debt

You cannot manage what you cannot see. Most teams underestimate their debt
because it is invisible until it bites them. A few lightweight practices help.

**Friction logs.** Ask engineers to note, in a shared document, any time they
spend more than thirty minutes working around a known problem rather than
solving it. After a month, patterns emerge.

**Change failure rate.** Track the percentage of changes that require a
follow-up fix within 48 hours. A rising rate is an early signal that code
quality is eroding.

**Time-to-first-test.** Measure how long it takes a new engineer to run the
test suite. If the answer is "we don't have a reliable test suite," that is
itself a significant piece of debt.

## Reducing Debt Without Stopping Product Work

The most common mistake is treating debt reduction as a separate project that
requires dedicated time. It rarely gets that time. A more durable approach is
to make debt reduction a habit woven into normal feature work.

The **boy scout rule** — always leave the code a little cleaner than you found
it — is a start. But it needs structure. A practical heuristic is the
**20% rule**: reserve roughly 20% of each sprint's capacity for technical work
that does not ship a feature. This covers bug fixes, test coverage, refactors,
and dependency upgrades. The exact percentage is less important than the habit
of protecting the time.

For larger items — a significant architectural change, a migration to a new
framework — you need a different approach. Treat them like products: write a
one-page proposal, estimate the benefit (usually in engineering-hours saved per
quarter), and prioritise against product work explicitly. Make the trade visible
to stakeholders rather than hidden in engineering estimates.

## Key Takeaways

- Technical debt is not inherently bad. Unmanaged debt at high interest is.
- The three types of debt — deliberate, inadvertent, and bit rot — call for
  different responses.
- Make debt visible through friction logs and change metrics before trying to
  manage it.
- Weave debt reduction into normal work with a protected time budget rather
  than treating it as a separate project.
