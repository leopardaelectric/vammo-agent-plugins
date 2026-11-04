# English — apply ASD-STE100 directly

Use the official **ASD-STE100 Simplified Technical English, Issue 9 (January 15, 2025)**:
[official site](https://asd-ste100.org/),
[official PDF](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf) and
[official FAQ](https://asd-ste100.org/STE_faq.html).

The standard contains writing rules and a controlled dictionary. Use its rules and dictionary for English content;
do not rename a synonym list, a prompt label or a word-count check as an STE dictionary or proof
of compliance. This guide is a route into the standard, not a replacement English standard.

## Use the normative source

Consult the official rules for the text being written and the dictionary for each ordinary word's
approved meaning and part of speech. Use the standard's technical noun and technical verb rules
for necessary domain terms. Keep product names, field names, commands, IDs and quoted error
messages literal; do not change evidence to fit a vocabulary rule.

For procedures, apply the rules for procedural writing: identify the actor, put a prerequisite
condition before its action, and make each step a clear instruction. For symptoms, context and
investigation questions, apply the descriptive-writing rules. Do not recast a source fact as an
instruction or promise to make it fit a procedure. Do not add a person or system as actor unless
the source names one. If the actor is unknown, use passive voice or describe the event without an
actor. Do not name the user as actor only because the source names an input control.

Use these rule locations in Issue 9. The rule numbers are the same as those in the official PDF;
these short pointers do not replace their explanations or the dictionary.

| Review | Normative locations | Apply to the draft |
| --- | --- | --- |
| Words | 1.1–1.4 | Read dictionary entries for approval, part of speech, meaning and word form. |
| Technical terms | 1.5–1.13 | Use eligible nouns and verbs; keep terminology consistent. |
| Verbs | 3.1–3.7 | Use permitted forms and tenses. Use active voice; descriptive text can use passive voice when the actor is unknown. |
| Steps | 5.1–5.5 | Limit sentences to 20 words. Use an imperative for an instruction, with its prerequisite first. |
| Description | 6.1–6.6 | Limit sentences to 25 words; group one topic per paragraph, with at most six sentences. |
| Punctuation and counting | 8.1–8.7 | Avoid semicolons. Apply the standard's counting rules for lists, literals, parentheses and hyphenated words. |
| Final review | 9.1–9.4 | Rebuild awkward sentences rather than replace words mechanically. Keep meaning and style consistent. |

A mechanical count alone cannot establish fidelity or approved vocabulary. A technical literal
does not make all surrounding jargon approved terminology.

Read the dictionary entry itself, not only a search result. In Part 2, an uppercase headword marks
an approved entry; a lowercase headword gives an unapproved form and its alternatives. Uppercase
words in examples or alternative columns do not approve the searched term. Read its row, part of
speech and meaning. Approval of a noun does not approve the same spelling as a verb.

Apply technical categories in context. Rule 1.12 permits **press** for computer input and **close**
for a user-interface process; their ordinary dictionary entries alone do not decide those uses.
Use an approved ordinary verb when it gives the same precise meaning. A software setting alone
does not turn every surrounding word into a technical term.

## Edit the complete draft before delivery

For every English artifact, make a final pass over the actual draft, not just the source or an
earlier response:

1. Read every ordinary word in the title, headings, body and criteria in the official dictionary.
   Read its entry for approval, meaning, part of speech and form. Consult the official manual through
   the host's permitted source access. A locally supplied official manual is also a source; this
   skill does not install or redistribute it. Record unsupported technical-term exceptions in
   separate review evidence. A word is not verified merely because it sounds clear.
2. Rewrite unapproved ordinary prose. Source prose does not grant vocabulary approval. A short
   title also needs this edit. Keep exact labels and quoted evidence literal; they do not exempt
   the surrounding prose. Keep the actor and the strength of every condition and obligation.
3. Check verb constructions and prerequisite order, then compare all facts with the source again.
   Use finite verbs for ordinary actions: a **When + actor + verb** clause can describe the action
   before its result. Ordinary forms such as “without showing” still need a rewrite. Apply the
   technical-noun exceptions only when their category and subject-field usage are supported.

These are known pitfalls within that pass. The alternatives were checked in the Issue 9 dictionary
for the indicated parts of speech. Recast these ordinary forms when they occur in editable prose;
choose an alternative only when its approved meaning fits. This table is neither the STE dictionary
nor a complete vocabulary check.

| Unapproved ordinary form | Rewrite direction |
| --- | --- |
| attempt (noun) | Use **try** as a verb when the actor is known. If the actor is unknown, give the input count with a passive verb and **times**; put the outcome count in a separate sentence. Do not use **tries** or the input verb as a count noun, and do not invent an actor. |
| preserve (verb) | Use **keep** as a verb when something must remain as supplied. |
| create (verb) | Use **make** as a verb when its approved meaning fits the required result. |
| investigate (verb) | Use **find** to identify what is known and what remains unknown. The work can end with the cause unknown. |
| repeated (adjective) | Issue 9 gives **again and again**. Do not use this form for an exact count. Name the item and count; use **same** only when the items agree in all details. |
| sometimes (adverb) | When the source describes an intermittent event, use **intermittently** in the title if that keeps its meaning. Keep any exact count in the body or criteria with **times**; do not replace the count with a vague frequency word. |
| alter (verb) | Use **change** as a verb. |
| comparison (noun) | Rebuild the sentence with **compare** as a verb; retain whether the work is complete. |
| action (noun) | Use **operation** for an executed process when that meaning fits. Use **step**, **task** or **procedure** only for those exact meanings. For frequency, use **time** or **times**. |
| arrive (verb) | Use **get** only when that meaning fits. For a required input, name when it is available. Do not add an arrival fact. |
| integrate (verb) | For a merged PR, write that the merge shows code integration in its repository. Do not use **integrate** as an ordinary verb. |
| success or failure outcomes | Keep the output obligation explicit: it must show which source-defined operation outcome occurred. Issue 9 gives **SATISFACTORY** (“agrees with all that is necessary”) for **successful**, and **UNSATISFACTORY** (“not satisfactory”) for **fail**. Use these adjectives only when they keep the operation outcome. Do not use **CORRECT** or **INCORRECT** when they would describe result quality instead. Keep **must**. |
| impact (noun) | Use **effect** only when that meaning fits. Keep the source's group: “The effect on [group] is unknown.” |
| whether | Use **if** for an indirect choice. Keep the same alternatives and conditions. |
| both | Use **the two** when the source names exactly two items. Do not use **both** in editable prose. |
| twice | Write **two times**. Do not use **twice** in editable prose. |
| appear (verb) | Use **show** only when the meaning is “be seen”; otherwise name the visible fact. |
| observe (verb) | Name the data or result. Use **see**, **obey** or **monitor** only when that exact meaning fits. |
| confirm (verb) | Use **make sure** only when that meaning fits; otherwise name the supplied result. |
| check (verb or noun) | Use **make sure**, **measure** or **examine** only when that meaning fits. Do not use **check** as an ordinary verb. Use the noun only for a procedure that makes sure something operates correctly or has no defects. Otherwise name the actual operation if the source gives it; do not infer an unspecified procedure. |
| explain (verb) | Issue 9 gives **tell**, which means give information or an order; it does not mean cause. Use **cause** only when the source asks about causation. |
| state (verb) | Make the fact the sentence subject: “The result shows [fact].” |
| say or report (verb) | Use **show** or **tell** only when the meaning stays the same. Name the actual output. |
| evidence (noun) | Use **indication** only when that meaning fits. Otherwise name the source data or result. |
| occurrence (noun) | Name what happened and how many times it happened. |
| inconclusive | Name what the data do not show. Keep the cause unknown when the source does not give it. |
| unverified | Name what data are absent. When accurate, write that supplied data do not show a result. Do not claim that the work did not occur. |
| answer, question or finding in investigation text | Name the expected result or known fact. For a comparison, write that the result shows if the values are the same or different. Do not write that a finding says something. |
| match (verb) or differ (verb) in a value comparison | Say that the values are the same when they agree in all details, or different when they are not the same. Require the comparison before recording the result. If the source says that an identifier comparison did not happen, write “The IDs were not compared.” Do not use “no result” to mean an unperformed comparison. |
| corresponding (adjective), identical (adjective), or duplicate (ordinary noun) | Issue 9 gives **RELATED** (“that has a relation”) for **corresponding** and **SAME** (“agrees in all details”) for **identical**. Use each only when its meaning fits. The local Issue 9 dictionary has no noun entry for ordinary **duplicate**. Describe only the relation the source supports; do not use **SAME** as a general replacement for **duplicate**. |
| fix (ordinary noun) | Issue 9 lists **fix** as a verb, not a noun. Use **solution** only when the source means “the answer to a problem.” Keep any prohibition on choosing or using a solution. |
| work or fail as an ordinary verb | Describe the exact outcome with an approved verb. Use **complete** only when the source means that the operation reached its end. |
| present perfect (`have` or `has` + past participle) | Use simple past or a present-state description. Do not add an actor or date. |

Then review these constructions:

- Rule 3.5 restricts verb forms ending in `-ing` to technical nouns and their modifiers. For an
  ordinary action, use a finite verb or an instruction. Describe what the user does and what the
  system shows. Exact labels and eligible technical nouns are separate cases; do not rewrite them
  just because their spelling ends in `-ing`. Rewrite ordinary “without + verb-ing” with a finite
  sentence or a “with no + noun” phrase when that keeps the fact. For example, use “with no result”
  instead of “without showing a result.”
- Rule 5.4 puts a required condition before the command. For example: “When the list is available,
  start the work.” Keep the prerequisite, its source and any prohibition on an earlier start.
  A dependency must not become a promise that its input has arrived.
  Apply this order to prohibitions too: “Until the list is available, do not start the test.”
  A prerequisite after “Do not start ...” still needs this edit; a later condition-first step
  does not repair the earlier instruction. If the condition governs the whole task, put it before
  the task action in the title or first instruction.
- A business or software context does not approve every ordinary verb or noun as a technical term.
  Use a term only when its technical category and actual company, industry or subject-field usage
  support it. If the source does not support a technical term, do not use it as one.
- Read each multiword verb's dictionary entry and meaning. Do not use a word combination for a
  meaning that the entry does not give. In Issue 9, **account for** means **make sure**; it does not
  mean **explain**.
- In an investigation, keep any explicit prohibition against choosing or applying a solution to
  the problem. The work can end with the cause unknown. Keep an unknown cause or input unknown.

After these edits, compare the draft with the source again. Grammar changes must keep
obligations, permissions, unknowns and literals. Do not add an STE review note to the card or to
an ordinary draft-only response. If the person asks for a language audit, report the checked
rules, vocabulary and remaining limits separately.

## Keep the claim

Synthetic source: “You must not start the test while `check_state=WAITING`.
The cause is unknown.”

Keep the prohibition, actor, condition and unknown cause. Splitting these sentences is safe.
Changing them to “You can start the test” changes the permission and loses the condition. The
field assignment is a synthetic literal, not an approved English term. Keep supplied literals
unchanged; establish technical noun approval from the actual subject field before claiming STE
compliance. The title and criterion examples in the main skill use CODE, INCORRECT and CORRECT
with their dictionary parts of speech. Their facts must still come from the source.

## Verification limits

If the official manual or required dictionary entries are unavailable, continue drafting clear
English and report normative verification as pending in the review evidence. Do not block a card
or switch languages because access failed. Do not claim formal STE compliance from this guide,
search snippets, model self-assessment or a checker alone. Verify the complete draft against the
official source when access is available; record the checked rules, terms and unresolved exceptions
separately from the card. Do not distribute the manual or third-party dictionary archives with
this skill. Link to the official source.
