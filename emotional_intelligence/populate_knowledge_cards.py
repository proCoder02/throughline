"""
One-time (safe to re-run): seeds emotional_intelligence.knowledge_cards with
a starter corpus of general psychoeducation cards, retrieved by
ei_adapter.get_relevant_knowledge_cards() and injected into /analyze, /chat,
and /chat/global's system prompt based on the user's most recent mood
(public.mood_logs) and/or their question text.

Cards 1-5 are distilled from "Cognitive-Behavioral Treatments for Anxiety
and Stress-Related Disorders" (Focus: Journal of Life Long Learning in
Psychiatry, 2021, https://pmc.ncbi.nlm.nih.gov/articles/PMC8475916/) --
techniques only, generalized away from any specific diagnosis (the paper
itself covers panic disorder/GAD/social anxiety/OCD/PTSD/prolonged grief,
written for a clinical audience; none of that diagnostic framing belongs in
a reply to someone who just said they feel anxious). Cards 6-12 cover
sadness/happiness/planning breadth from separate, well-established sources,
cited individually below.

Idempotent via ON CONFLICT (title) -- editing a card's content here and
re-running updates it in place rather than duplicating rows.

Usage:
    python populate_knowledge_cards.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
except ImportError:
    print("Missing dependency. Run: pip install psycopg2-binary")
    sys.exit(1)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL is not set. Add it to your .env file.")
    sys.exit(1)

PAPER_CITATION = (
    "Cognitive-Behavioral Treatments for Anxiety and Stress-Related Disorders, "
    "Focus: J Life Long Learning in Psychiatry (2021)"
)

CARDS = [
    {
        "title": "Cognitive restructuring for anxious or negative thoughts",
        "moods": ["anxious", "stressed", "sad", "frustrated"],
        "situation": "Stuck on a specific worried or self-critical thought that keeps looping",
        "framework_name": "CBT: cognitive restructuring",
        "framework": (
            "Name the specific automatic thought, then examine it like evidence rather than fact: "
            "what actually supports it, what contradicts it, and what would you tell a friend who had "
            "this exact thought? Land on a more balanced alternative thought, not a forced positive one."
        ),
        "example_prompt": (
            "What's the thought running through your head right now, in one sentence? Let's look at "
            "what actually backs it up, and what doesn't."
        ),
        "source_citation": PAPER_CITATION,
        "scope": "general",
    },
    {
        "title": "Graded exposure to a feared or avoided situation",
        "moods": ["anxious", "stressed"],
        "situation": "Avoiding something specific out of anxiety, and the avoidance itself is now the problem",
        "framework_name": "CBT: graded exposure",
        "framework": (
            "Break the feared situation into a small ladder of steps from least to most anxiety-provoking, "
            "and take the smallest one first, staying with the discomfort until it naturally eases rather "
            "than escaping it -- avoidance is what keeps the fear intact, not the situation itself."
        ),
        "example_prompt": (
            "What's the smallest version of this you could actually try this week -- not the whole thing, "
            "just one small step?"
        ),
        "source_citation": PAPER_CITATION,
        "scope": "general",
    },
    {
        "title": "Behavioral experiment to check a feared prediction",
        "moods": ["anxious", "stressed"],
        "situation": "A specific feared prediction about what will happen (\"if I do X, Y bad thing will happen\")",
        "framework_name": "CBT: behavioral experiment",
        "framework": (
            "Turn the fear into a testable prediction, then design the smallest safe way to actually test "
            "it, instead of assuming it's true. Compare what was predicted against what actually happened."
        ),
        "example_prompt": "What exactly are you predicting will happen? Is there a small, safe way to actually find out?",
        "source_citation": PAPER_CITATION,
        "scope": "general",
    },
    {
        "title": "Grounding for acute anxiety or physical panic symptoms",
        "moods": ["anxious", "stressed", "calm"],
        "situation": "Anxiety showing up as physical symptoms right now (racing heart, tight chest, can't focus)",
        "framework_name": "Mindfulness-based grounding",
        "framework": (
            "Shift attention to the present moment through the senses (e.g. naming 5 things you can see, "
            "4 you can touch, 3 you can hear) and slow, deliberate breathing -- this interrupts the "
            "physical spiral rather than trying to reason your way out of it first."
        ),
        "example_prompt": "Before we think this through, let's slow the physical side down first -- can you name 3 things you can see right now?",
        "source_citation": PAPER_CITATION,
        "scope": "general",
    },
    {
        "title": "Grief that hasn't started to ease",
        "moods": ["sad"],
        "situation": "Grief over a loss that feels stuck or isn't softening the way they expected",
        "framework_name": "Grief-focused CBT",
        "framework": (
            "Distinguish between the loss itself and the secondary struggle of feeling stuck in it -- gently "
            "encourage reconnecting with meaningful routines and relationships alongside the grief, not "
            "instead of it, rather than treating any re-engagement with life as a betrayal of the loss."
        ),
        "example_prompt": "It makes sense this still feels heavy. Is there anything, even small, you used to enjoy that you've pulled away from since?",
        "source_citation": PAPER_CITATION,
        "scope": "clinical_adjacent",
    },
    {
        "title": "Behavioral activation for low mood",
        "moods": ["sad"],
        "situation": "Low motivation and withdrawal that's deepening a low mood (the classic sadness-inactivity spiral)",
        "framework_name": "Behavioral activation",
        "framework": (
            "Motivation tends to follow action, not precede it -- schedule one small, concrete, values-"
            "aligned activity regardless of motivation, rather than waiting to feel like doing it first."
        ),
        "example_prompt": "What's one small thing, even 10 minutes, you could do today that you'd normally enjoy -- not because you feel like it, but as an experiment?",
        "source_citation": "Jacobson et al., behavioral activation for depression",
        "scope": "general",
    },
    {
        "title": "Self-compassion reframe for harsh self-criticism",
        "moods": ["sad", "frustrated"],
        "situation": "Being unusually harsh or critical toward themselves about a mistake or setback",
        "framework_name": "Self-compassion (Neff)",
        "framework": (
            "Ask what you'd say to a friend in this exact situation, then notice the gap between that and "
            "what you're saying to yourself -- the goal isn't to excuse the mistake, it's to respond to it "
            "the way you would for someone you cared about."
        ),
        "example_prompt": "If a close friend told you they'd done exactly this, what would you actually say to them?",
        "source_citation": "Kristin Neff, self-compassion research",
        "scope": "general",
    },
    {
        "title": "Savoring an enjoyable experience",
        "moods": ["happy", "excited"],
        "situation": "Something good just happened and it's worth deliberately extending, not just noting in passing",
        "framework_name": "Savoring (Bryant)",
        "framework": (
            "Deliberately slow down on a positive experience instead of rushing past it: notice what led to "
            "it, how it feels physically, and mentally 'bookmark' it -- savoring measurably extends the "
            "emotional benefit of a positive experience compared to letting it pass unremarked."
        ),
        "example_prompt": "That's great -- what part of that felt best? Worth sitting with that for a second.",
        "source_citation": "Fred B. Bryant, \"Savoring: A New Model of Positive Experience\"",
        "scope": "general",
    },
    {
        "title": "Gratitude reflection to reinforce positive mood",
        "moods": ["happy", "calm"],
        "situation": "Good mood or a positive stretch worth reinforcing into a habit, not just a one-off",
        "framework_name": "Gratitude practice (Emmons & McCullough)",
        "framework": (
            "Name a small number of specific good things and, briefly, why they happened -- specificity "
            "and attribution (not a generic 'grateful for my life') is what makes this reinforce mood over "
            "time rather than feeling like a rote exercise."
        ),
        "example_prompt": "What's one specific good thing from today, and what actually made it happen?",
        "source_citation": "Emmons & McCullough, gratitude intervention research",
        "scope": "general",
    },
    {
        "title": "GROW model for a stuck decision or plan",
        "moods": ["planning", "neutral", "stressed"],
        "situation": "Stuck on a plan or decision with no clear next step",
        "framework_name": "GROW model (Whitmore)",
        "framework": (
            "Work through four questions in order: what's the actual Goal, what's the honest current "
            "Reality, what Options exist (including ones that feel unrealistic at first), and what are "
            "you actually Willing to commit to as a next step -- skipping straight to options without "
            "naming the real goal is the most common way planning conversations stall."
        ),
        "example_prompt": "Before we get to options -- what's the actual goal here, in one sentence?",
        "source_citation": "John Whitmore, \"Coaching for Performance\"",
        "scope": "general",
    },
    {
        "title": "Implementation intentions (if-then planning)",
        "moods": ["planning"],
        "situation": "A goal or intention exists but keeps not translating into actually doing it",
        "framework_name": "Implementation intentions (Gollwitzer)",
        "framework": (
            "Convert a vague intention into a concrete 'if situation X, then I will do Y' plan tied to a "
            "specific time, place, or trigger -- this measurably increases follow-through compared to a "
            "bare goal or willpower alone."
        ),
        "example_prompt": "When exactly, and where, will you actually do this -- can we turn it into an 'if this happens, then I'll do that'?",
        "source_citation": "Peter Gollwitzer, implementation intentions research",
        "scope": "general",
    },
    {
        "title": "Eisenhower matrix for prioritizing when overwhelmed",
        "moods": ["planning", "stressed"],
        "situation": "Too many tasks or decisions at once, unclear what actually needs attention first",
        "framework_name": "Eisenhower matrix",
        "framework": (
            "Sort what's on your plate by urgent-vs-not and important-vs-not: do what's urgent and "
            "important now, schedule what's important but not urgent, delegate or shorten what's urgent "
            "but not important, and drop what's neither -- most overwhelm comes from treating everything "
            "as equally urgent."
        ),
        "example_prompt": "Of everything on your plate, what's actually important AND due soon -- versus just loud?",
        "source_citation": "Popularized by Stephen Covey, \"The 7 Habits of Highly Effective People\"",
        "scope": "general",
    },
]

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

for card in CARDS:
    cur.execute(
        """
        INSERT INTO emotional_intelligence.knowledge_cards
            (title, moods, situation, framework_name, framework, example_prompt, source_citation, scope)
        VALUES (%(title)s, %(moods)s, %(situation)s, %(framework_name)s, %(framework)s,
                %(example_prompt)s, %(source_citation)s, %(scope)s)
        ON CONFLICT (title) DO UPDATE SET
            moods = EXCLUDED.moods,
            situation = EXCLUDED.situation,
            framework_name = EXCLUDED.framework_name,
            framework = EXCLUDED.framework,
            example_prompt = EXCLUDED.example_prompt,
            source_citation = EXCLUDED.source_citation,
            scope = EXCLUDED.scope,
            updated_at = now()
        """,
        card,
    )
    print(f"Upserted: {card['title']}")

cur.execute("SELECT count(*) FROM emotional_intelligence.knowledge_cards")
print(f"\nTotal knowledge_cards rows: {cur.fetchone()[0]}")

cur.close()
conn.close()
