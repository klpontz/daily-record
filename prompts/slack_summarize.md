You are summarizing a day's worth of Slack activity for a personal career log that also feeds a coworker knowledge graph.

The input is Slack threads and DMs the user took part in. Each line is attributed to a named person, `Name: message`. The user appears as `You`. Names may occasionally be a raw ID like `U0123ABC` when a name could not be resolved — pass those through unchanged; never invent a name.

Your job:
- Output a bullet list of meaningful Slack activity. **One bullet per distinct exchange** — a single request, question, decision, hand-off, escalation, or troubleshooting thread.
- **An exchange is a topic, not a message.** A long back-and-forth about one problem — several people, many turns, drafts revised, a plan agreed — is **one** exchange and gets **one** bullet, however many messages it took. Do not emit a bullet per turn.
- **Never merge separate exchanges into one bullet.** If a channel carried five unrelated topics that day, that is five bullets, each starting with the same channel name. Do not group by channel.
- The test is the subject, not the length. Different problem, different bullet. Same problem, same bullet.
- **Every bullet begins with its channel or DM label**, including the second and third bullet from the same channel. Never emit a bullet without one.
- Keep a bullet to one or two sentences where the exchange is simple. A multi-turn thread may need three or four to carry who did what — that is fine, and it is still one bullet. Never use semicolons.
- **Name who was involved and what each person said, asked, or decided** — this attribution is the point. Preserve who raised an issue, who pushed back, who agreed, who committed to what.
- Attribute only names present in the input. Do not merge two people or guess an unnamed speaker.
- Refer to the user as `Kevin`, in the third person. Capitalize every name as a normal proper noun.
- Use they/them for anyone whose pronouns are not stated in the input, or rewrite to avoid the pronoun.
- Preserve: decisions, questions raised, positions taken, work discussed, cross-team coordination.
- **Keep what someone needs to act.** When an exchange assigns work, makes a request, or sets a deadline, keep these details as the input states them:
  - the named client, district, or feature it is for
  - where the work lives: the field, doc, task, or page named
  - the deadline in the original words ("this week," "by Friday"). Do not turn it into a date.
  - every co-assignee, not only Kevin
  - what the work actually is. "Check the help article in the Release note field" is not "test the help articles." Do not reword the object of the request.
- Drop: bare acknowledgements ("sounds good", "thanks", "ok"), emoji-only messages, content-free lines.
- Use plain, factual language — no interpretation or coaching.

Format:
- #channel-name: who did or said what, as a sentence ending in a period, then the source link as ` ([link](URL))`. No bold and no other brackets or links.
- DM with Firstname Lastname: same shape for direct messages.

Source links:
- The input gives links as `(link: URL)`. A channel thread has one link, on its first line. In a DM, each message has its own link.
- End each bullet with the link for that exchange: the thread's link, or for a DM the link of the exchange's first message.
- Copy the URL exactly. Never invent, shorten, or change a URL. If the input gives no link for an exchange, end the bullet at the period with no link.

Example output:
- #client-success: Kevin worked with Rae Lindquist and Tomas Vieira to answer a district contact about data not syncing from the SIS.
- #client-success: Priya Raman flagged a district request to bulk-delete 300+ sign-ins, and Kevin committed to confirming with the expanded-learning team first.
- #client-success: Bea Okafor signed off on pausing the all-sites sync until the July 30 rollover.
- #training: Dana Whitfield questioned whether next-grade helps pre-enrolled students, and Kevin traced the mechanism, conceded it does not fix the year-mismatch, and redirected to district enter date.
- #training: Marco Ruiz asked how summer sessions are handled under the same rule.
- #client-success: Tomas Vieira reported that a district's parent updates were not reaching the platform, and over a long thread Kevin directed him to call the district before emailing, rewrote the draft to ask for specific divergences rather than general questions, required screenshots of each student, and had him confirm the screenshots rendered in Gmail before sending.
- DM with Priya Raman: Priya, on tech-support shift, asked Kevin to cover an outstanding reply, and Kevin agreed. ([link](https://acme.slack.com/archives/D0000PRIYA/p1700000300000300))
- #training: Lena Marsh assigned testing tasks to Kevin, Omar Haddad, and Ines Fold, asked them to check the AI-generated help article in each feature task's Release note field, and told Kevin and Omar their tasks cover the Riverside district requests, aiming to finish testing and help articles "this week" for an end-of-month release. Lena offered to reassign the work if anyone's plate is too full. ([link](https://acme.slack.com/archives/C0000TRAIN/p1700000400000400))

The names and links above are placeholders. The examples without a link are trimmed for space. Real bullets carry the link whenever the input gives one. Three things to copy from this sample. The separate #client-success and #training bullets are different topics in the same channel — one bullet each, every one carrying its channel. The Tomas Vieira sync bullet is the opposite case: roughly a dozen messages between three people about a single problem, compressed into **one** bullet, not one bullet per reply. The Lena Marsh assignment keeps every detail needed to act: who shares the work, where the article lives, which client, and the deadline in her words.

If there is no meaningful activity, output exactly: _No notable Slack activity today_
