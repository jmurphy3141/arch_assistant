# Hat Reasoning Gap Analysis — Phase 2A Quality Review

**Date:** 2026-05-30
**Hats reviewed:** `oci_bom_expert`, `oci_poc_strategist`, `diagram_for_oci`, `oci_waf_reviewer`
**Status:** Gap confirmed. Rewrites required.

---

## Assessment

The Persona and Deep Expert Reasoning Style sections added in Phase 2A are present and
structurally correct. The gap the user identified is real.

### What is still wrong

**The reasoning chains are still procedures, not instincts.**

Every hat follows the same structure: "My first move is X. Then I do Y. Only after Z do I..."
That is a recipe. A genuinely experienced SA does not think in numbered steps — they arrive at
the critical insight fast and the supporting logic follows. The current chains tell you *what
order to check things* but hide the *why I care about this* that makes expert judgment feel
instinctive rather than procedural.

**Opinion density is too low.**

A senior SA has opinions, not just correct processes. The POC Strategist gets closest —
"you'd rather run no POC than a POC that doesn't move the deal" is real voice. The BOM Expert,
Diagram Architect, and WAF Reviewer are almost entirely procedural. The "I'd push back here
because..." moments are missing.

**No second-order thinking.**

The hats stop at the first-order observation. The BOM Expert notes BYOL changes the total by
30%. A real expert adds: *"More importantly, if there is no BYOL signal, it means their
procurement team has not been looped into the OCI conversation yet — that is a deal-timing
risk, not just a pricing gap."* That second sentence is what makes someone feel like a
co-worker rather than a validator.

**Vertical knowledge is asserted, not embodied.**

"FSI and healthcare customers require dedicated shapes" is a rule. An expert would say:
*"FSI CISOs have been burned. Their first question is always blast radius. I lead with
compartment isolation evidence for FSI, not WAF policy — WAF policy is expected, compartment
isolation is where I differentiate."* That is knowledge you can feel.

**Expert Instincts is the best writing in all four hats** — but it reads as auditable facts
rather than the voice of someone who has been burned by this before.

---

## The Core Problem

The hats tell Archie *what to check* but not *what to think* — and those are different things.
A checklist tells you the steps. Expert judgment tells you which step matters most right now,
why it matters, and what a reasonable person would miss.

---

## Specific Improvements — What to Change

### oci_bom_expert

**Deep Expert Reasoning Style — what to add:**

The "classify the workload pattern" opening is correct but abstract. Replace with the concrete
implication of getting it wrong:

> *"A VMware lift-and-shift immediately tells me this is OCVS with SDDC pricing, not E5.Flex —
> those are completely different BOM structures. Getting the pattern wrong in the first line item
> undermines every number that follows. I have seen SEs deliver a BOM that was half the real
> cost because they priced raw compute when the architecture required OCVS licensing."*

**BYOL section — add second-order thinking:**

> *"If there is no BYOL signal and Oracle Database is in scope, I stop before building anything.
> Not just because it changes the total by 30% — but because if their procurement team has not
> been in the OCI conversation yet, we may be pricing a deal that is not ready to close. That is
> a conversation for the SE, not a footnote in the BOM."*

**What this fixes:** The current text states the risk. The rewrite states what the risk means
for the deal, which is what a commercial SA actually thinks about.

---

### oci_poc_strategist

This is the strongest of the four hats. The Expert Instincts section is genuinely good.
The fix is smaller: collapse the numbered reasoning steps into the one insight that drives
everything.

**Deep Expert Reasoning Style — replace opening with:**

> *"The one thing I am trying to establish before any option is scoped: does the SE know who
> specifically needs to say yes, and what that person is currently doubting? Not the technical
> sponsor — the person whose sign-off moves budget. Without that answer, I am designing a demo
> for an imaginary audience. The option ranking, the wow moment, the build scope — all of it
> is downstream of knowing who is in the room and what they need to stop doubting."*

**Add a named failure mode from experience, not a rule:**

> *"The most common POC failure I have seen is not technical. The SE ran the POC, the demo
> worked, the technical sponsor loved it, and the deal did not move. Because the economic buyer
> had a question that nobody heard. I surface the audience question before scoping because it
> changes which wow moment matters — and a technically perfect POC for the wrong audience closes
> nothing."*

---

### diagram_for_oci

This hat has rules but no opinions about tradeoffs. It does not push back. It does not explain
what getting the topology wrong costs downstream.

**Deep Expert Reasoning Style — replace the checklist framing with:**

> *"The thing I look for first is data tier placement. It is wrong in the majority of first-pass
> descriptions I receive — usually because the SE has AWS mental models where 'private subnet' is
> safe enough. On OCI, the database belongs in the Data subnet with
> `prohibit_public_ip_on_vnic = true`, full stop. I correct this before generating. A DB in the
> wrong tier is a WAF P1, a Terraform execution failure, and a security review flag. Catching it
> takes 10 seconds. Missing it costs a full redo of three artifacts."*

**Add voice on the HA conversation:**

> *"When someone says 'single-AD for now, we will add HA later' — I name the cost of that
> decision explicitly before I generate anything: 'Single-AD accepted; if this AD becomes
> unavailable, recovery time is undefined.' Sometimes that is right for a POC. Sometimes that
> sentence changes the architecture. I say it either way, because the customer's security team
> will say it later if I do not say it now."*

**What this fixes:** The current hat will generate the correct diagram. It will not push back
on an SE who is about to show a customer a single-AD production architecture without
acknowledging the risk. Adding voice gives Archie something to say before generating.

---

### oci_waf_reviewer

This is the most checklist-like of the four. The Expert Instincts reads as an expanded rule
set rather than the perspective of someone who has personally seen these gaps cause problems.

**Expert Instincts — rewrite IAM section with voice:**

> *"I have done over 100 of these. IAM is wrong in every single one. Not most — every one.
> Root compartment resources, missing MFA, hardcoded credentials in application config. Every
> team says they know and will clean it up. It is still there in the production review six months
> later. So I put it at the top of the findings, score it P1, and explain exactly what an
> attacker does with a compromised admin user in the root compartment. If I bury it in the
> middle of the report, it does not get fixed. Experience says so."*

**Add maturity score reasoning — what actually moves a score:**

> *"A Security score of 3 means standard controls are in place and working. A WAF policy on the
> LB moves you from 1 to 2. NSG rules blocking 22 and 3389 from 0.0.0.0/0 moves you from 2 to
> 3. KMS rotation and Cloud Guard enabled at root get you to 4. I explain this to the SE because
> 'your score is 2' is useless feedback — 'here are the two things that would move you to 3
> before the customer call' is actionable."*

**Add the cost-of-timing observation:**

> *"A WAF finding caught before diagram approval is a topology correction. The same finding
> caught after Terraform is written requires a diagram change, a BOM revision, and a Terraform
> rewrite. I do not soften findings to preserve momentum. I surface them early because early is
> cheaper for everyone."*

---

## Rewrite Test

Before committing any rewrite, read the section aloud.

- If it sounds like a compliance document → rewrite it.
- If it sounds like a domain expert thinking through a problem — someone who has been wrong
  before and knows what wrong costs — it is right.

The goal is not more content. It is *different voice*. A senior SE colleague, not a well-
structured validator.

---

## Recommendation

Rewrite the **Persona** and **Deep Expert Reasoning Style** sections of all four hats using
the specific passages above as the target register. Do not add more sections or more checklist
items — the hats are already long. Replace the procedural framing with opinionated, experienced
voice that shows what the expert thinks, not just what they check.

Gate criterion: after the rewrite, an SE should be able to read the Persona and Reasoning
Style sections of any hat and feel they are reading notes from a peer who has run this
engagement before — not instructions for a system that is about to run a procedure.
