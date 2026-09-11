"""
WHAT:
    Two behaviours of the demo chat page: who gets their conversation
    back on a reload, and the greeting that is on screen before anything
    is typed.

WHO KEEPS THEIR HISTORY:
    A NAMED account, picked from the list, is a person on a device that
    is theirs - reloading should not lose their conversation. The
    pasted-token path has no identity behind it, so it keeps nothing.
    See the thread-storage note in demo_ui.py for what persistence costs
    on a surface that reads real customers.

WHY THEY ARE TESTED TOGETHER:
    They are the same moment - what a person sees when the page loads -
    and they pull against each other. The greeting must appear every
    time, which means it cannot be a stored message. The transcript must
    appear for a named account and must NOT for a guest, which means the
    restore has to be conditional without the greeting becoming so.
    Getting one right by breaking the other is the likely regression.

WHY THE GREETING MUST NOT BE A MESSAGE, stated here because the test
below looks pedantic without it:
    Pushing it into thread.messages would send it to /chat, store it in
    the server's session, and put an assistant turn into the history the
    model reads - a sentence no lookup vouches for, which the scope rule
    and the grounding checks never inspected because neither the model
    nor a tool produced it. Rendering it costs nothing and risks nothing.

HOW THESE ARE CHECKED:
    Against the served page source. There is no JS runtime in the suite,
    so these assert the SHAPE of the code that produces the behaviour -
    the same approach test_prompt_injection.py takes to a prose rule.
    Real behaviour was verified in a browser; this is the regression net.
"""

import inspect
import re

from app.api.routes import demo_ui

PAGE = demo_ui.PAGE
GREETING = "Hi! Welcome to Zatch"


class TestHistoryPersistsOnlyForANamedAccount:
    """WHO KEEPS THEIR HISTORY, and why it is a split rather than a flag.

    Signing in as a person from the picker is a statement that this
    device is theirs, so a reload should put them back where they were.
    A pasted raw JWT has no identity behind it - it is a credential
    somebody is holding, often on a machine that is not theirs - so
    nothing touches the disk.

    These tests read the served page source; there is no JS runtime in
    the suite. The behaviour itself was driven in a browser.
    """

    def test_the_guest_account_is_named_explicitly(self):
        assert 'const GUEST_ACCOUNT = "token"' in PAGE
        assert "function isGuest()" in PAGE

    def test_a_guest_writes_nothing_to_disk(self):
        """THE ORDERING IS THE PROPERTY. The guest check has to come
        BEFORE the write, or a guest's transcript lands in localStorage
        and the split is decorative."""
        body = PAGE.split("function saveThreads(list)")[1].split("\n}")[0]
        guard_at = body.index("if (isGuest()) return;")
        write_at = body.index("localStorage.setItem")
        assert guard_at < write_at, "a guest's history is written to disk"

    def test_a_guest_loads_nothing_from_disk(self):
        body = PAGE.split("function hydrateThreads()")[1].split("\n}")[0]
        guard_at = body.index("if (isGuest())")
        read_at = body.index("localStorage.getItem")
        assert guard_at < read_at, "a guest inherits saved history"

    def test_a_named_account_is_keyed_to_itself(self):
        """One account must never read another's transcript back."""
        body = PAGE.split("function key()")[1].split("\n}")[0]
        assert "account" in body, "the storage key is not per-account"

    def test_every_storage_access_is_wrapped(self):
        """localStorage throws outright in a private window and wherever
        site data is blocked. Losing saved threads must degrade the demo,
        never break it."""
        for fn in ("hydrateThreads", "saveThreads"):
            body = PAGE.split("function " + fn)[1].split("\n}")[0]
            if "localStorage" in body:
                assert "try {" in body and "catch" in body, fn + " is unguarded"

    def test_signing_out_drops_the_list_from_memory(self):
        """The saved copy stays - signing out is not "delete my history",
        that is what Clear does - but the next account to sign in must
        not inherit the last one's list."""
        body = PAGE.split("function signOut()")[1].split("\n}")[0]
        assert "THREADS = []" in body


class TestASavedTranscriptCannotOutliveTheServerSession:
    """The client and the server hold two halves of one conversation, and
    only the server's half expires on its own. A transcript saved today
    would otherwise still be on screen next week, while the session
    behind it lapsed after a day - the reader scrolls up, sees their
    history, asks a follow-up about it, and the model has no idea."""

    def test_stale_threads_are_pruned_on_load(self):
        assert "function pruneStale(" in PAGE
        body = PAGE.split("function hydrateThreads()")[1].split("\n}")[0]
        assert "pruneStale(" in body

    def test_the_cutoff_comes_from_the_server(self):
        """Hardcoding it would let the two drift apart the first time
        anybody changes the TTL."""
        assert "s.session.ttl_minutes" in PAGE
        assert "SESSION_TTL_MINUTES" in PAGE

    def test_the_fallback_errs_short(self):
        """If /demo/stats failed there is no real number to use.
        Dropping a transcript early is a smaller wrong than showing one
        the server has forgotten."""
        line = [l for l in PAGE.splitlines()
                if l.strip().startswith("let SESSION_TTL_MINUTES")][0]
        fallback = int(line.split("=")[1].strip().rstrip(";"))
        assert fallback <= 60, "the fallback outlives a short server TTL"

    def test_the_server_actually_sends_it(self):
        """The page can only read the real TTL if the endpoint puts it
        there. Asserted against the route's source rather than a live
        call, because /demo/stats counts collections and would drag a
        database into an otherwise hermetic file."""
        from app.memory.session_store import SESSION_TIMEOUT_MINUTES

        source = inspect.getsource(demo_ui)
        assert '"session": {"ttl_minutes": SESSION_TIMEOUT_MINUTES}' in source
        assert SESSION_TIMEOUT_MINUTES == 24 * 60


class TestWhatIsStillTrueForEveryone:
    def test_a_session_id_cannot_repeat_within_a_browser(self):
        """Six base36 characters collide rarely but not never, and a
        collision resolves to a session key scoped by the SAME user - so
        the one thing it could resurrect is this account's own previous
        conversation. The clock prefix makes that impossible rather than
        unlikely."""
        assert 'id: "demo-" + Date.now().toString(36)' in PAGE

    def test_the_access_code_still_persists(self):
        """Deliberately separate from all of this. The code is a door key
        typed once, not conversation content, and sessionStorage clears
        it when the tab closes."""
        assert 'sessionStorage.setItem("zatch.code"' in PAGE


class TestTheGreetingIsThereBeforeAnythingIsTyped:
    def test_the_greeting_text_is_served(self):
        assert GREETING in PAGE
        assert "How can I help you today?" in PAGE

    def test_it_is_rendered_on_every_chat_render(self):
        """Drawn rather than stored, so it survives switching threads and
        cannot drift out of the transcript."""
        body = PAGE.split("function renderChat()")[1].split("thread.messages.forEach")[0]
        assert "greeting();" in body

    def test_it_is_never_pushed_into_the_conversation(self):
        """The one thing that would make it unsafe: a greeting in
        thread.messages travels to /chat, into the session store, and
        into the history the model reads."""
        body = PAGE.split("function greeting()")[1].split("function renderChat")[0]
        assert "thread.messages" not in body, (
            "the greeting is being stored as a real message - it would "
            "reach the model as an assistant turn nothing vouched for"
        )
        assert "add(" in body, "it should render through the normal bubble path"

    def test_it_renders_as_a_bot_bubble(self):
        body = PAGE.split("function greeting()")[1].split("function renderChat")[0]
        assert '"msg bot"' in body

    def test_it_uses_text_content_like_every_other_bubble(self):
        """add() writes with textContent, never innerHTML - the rule that
        keeps seller-written product names out of the DOM as markup."""
        body = PAGE.split("function add(text, cls)")[1].split("function")[0]
        # Comments stripped first: add()'s own comment says "textContent,
        # never innerHTML", so a naive search finds the word in the rule
        # that forbids it.
        code = re.sub(r"//.*", "", body)
        assert "textContent" in code and "innerHTML" not in code

    def test_the_starter_panel_no_longer_greets_as_well(self):
        """Two hellos in a row reads as a bug. The panel now does the
        other half of the job - saying what is worth asking about."""
        assert "Ask me about your Zatch account" not in PAGE
        assert "Here's what I can look up" in PAGE


class TestTheChatContractIsUnchanged:
    """The scope rule, the grounding checks and the session store all
    live behind /chat. This change is presentational, and these pin that
    it stayed that way."""

    def test_only_the_message_and_session_id_are_sent(self):
        body = PAGE.split("body: JSON.stringify({message: text")[1][:80]
        assert "session_id: thread.id" in body

    def test_the_greeting_is_not_in_the_request_body(self):
        assert "message: " + repr(GREETING) not in PAGE
        sends = re.findall(r"JSON\.stringify\(\{message:[^}]*\}", PAGE)
        for send in sends:
            assert "Welcome to Zatch" not in send


class TestTheConversationsListIsGone:
    """It was removed, and removing it changes more than one screen.

    THE STORAGE MODEL FOLLOWS FROM THE UI. While a list existed, keeping
    thirty threads was reasonable - every one of them was reachable.
    Without it a second thread is unreachable the moment a third starts,
    so keeping them would leave somebody's real order history on disk
    with no way to open, review or delete it. Storage nobody can see is
    storage nobody can clear."""

    def test_the_screen_is_removed(self):
        for gone in ("screenThreads", "threadRows", "threadEmpty",
                     "threadsLede", "Conversations"):
            assert gone not in PAGE, gone + " survived"

    def test_its_functions_are_removed(self):
        for gone in ("renderThreads", "openThread", "deleteThread"):
            assert gone not in PAGE, gone + " survived"

    def test_no_dead_helpers_are_left_behind(self):
        """ago() only ever formatted timestamps for the list rows."""
        assert "function ago(" not in PAGE

    def test_the_screen_switcher_no_longer_knows_it(self):
        body = PAGE.split("function showScreen(name)")[1].split("\n}")[0]
        assert '"Threads"' not in body
        assert '"threads"' not in body

    def test_back_goes_straight_to_the_picker(self):
        """The list used to sit between the chat and signed-out. With it
        gone there is no intermediate step to land on."""
        body = PAGE.split("function goBack()")[1].split("\n}")[0]
        assert "signOut()" in body
        assert "threads" not in body

    def test_only_the_current_conversation_is_stored(self):
        body = PAGE.split("function upsertThread()")[1].split("\n}")[0]
        assert "saveThreads([thread])" in body
        assert "unshift" not in body, "threads are still accumulating"

    def test_starting_a_new_one_ends_the_old_one_on_the_server(self):
        """Otherwise the replaced conversation sits in Redis for the rest
        of the 24-hour TTL, holding real orders that no screen can show
        and no button can clear."""
        body = PAGE.split("async function newThread()")[1].split("thread = {")[0]
        assert 'method: "DELETE"' in body
        assert '"/chat/session/"' in body

    def test_it_does_not_block_on_that_delete(self):
        """Deliberately unlike clearChat. There the user asked for the
        data to be gone, so a failure must stop and say so. Here they
        asked for a new conversation - failing to tidy the old one is
        worth logging and nothing else."""
        body = PAGE.split("async function newThread()")[1].split("thread = {")[0]
        assert ".catch(() => {})" in body
        assert "await fetch" not in body

    def test_the_replacement_is_saved_before_anything_is_typed(self):
        """A REAL BUG, CAUGHT BY DRIVING IT. upsertThread otherwise runs
        only after a message is sent, so between pressing New and typing
        anything the stored copy still described the conversation this
        one replaced - whose server session had just been deleted. A
        reload in that window restored a transcript the model had already
        forgotten."""
        body = PAGE.split("async function newThread()")[1].split("showScreen")[0]
        assert "upsertThread();" in body


class TestTheStarterChips:
    """Four fixed questions, one per intent a shopper arrives with.

    EACH WAS DRIVEN AGAINST REAL DATA BEFORE BEING OFFERED, because a
    starter that misses makes "I couldn't find that" the product's
    opening line. Two candidate phrasings were rejected on evidence:
    "Do you have any offers?" called no tool at all and asked the user
    to disambiguate, and "Track my delivery" produced the same list as
    "Where is my order?" - two slots, one answer."""

    def test_there_are_exactly_four(self):
        block = PAGE.split("const STARTERS = [")[1].split("];")[0]
        assert len([l for l in block.splitlines() if l.strip().startswith('"')]) == 4

    def test_they_are_the_verified_set(self):
        for q in ("Where is my order?", "Show me trending products",
                  "What should I buy?", "What's in my cart?"):
            assert q in PAGE, q + " is missing"

    def test_the_rejected_phrasings_are_not_offered(self):
        """Scoped to the chip list, not the whole page: the comment above
        STARTERS quotes both rejected phrasings to say why they went, and
        that comment is served with the page."""
        block = PAGE.split("const STARTERS = [")[1].split("];")[0]
        assert "Do you have any offers?" not in block, (
            "this one called no tool and answered with a disambiguation menu"
        )
        assert "Track my delivery" not in block, (
            "this produced the same list as 'Where is my order?'"
        )
        # Every phrasing of the deals intent, each measured. Nothing
        # lists discounts, so these land on a disambiguation question
        # instead of a lookup - see the note above STARTERS.
        for unreliable in ("Any discounts right now?",
                           "Which products are on sale?",
                           "Show me discounted products"):
            assert unreliable not in block, (
                unreliable + " is not reliably grounded - an intermittent "
                "starter passes the spot check and fails the user"
            )

    def test_none_of_them_names_a_product(self):
        """A chip naming a product goes stale the moment the catalogue
        changes. These four lean on the account's own records and on
        general listings, so they cannot miss."""
        block = PAGE.split("const STARTERS = [")[1].split("];")[0]
        for banned in ("Frame", "shirt", "Lenin", "Labham"):
            assert banned not in block

    def test_the_lede_paragraph_is_gone(self):
        assert "Orders and delivery, the catalogue" not in PAGE
        body = PAGE.split("function welcome()")[1].split("\n}")[0]
        assert "createElement(\"p\")" not in body, (
            "the starter screen has prose between the heading and the chips again"
        )

    def test_the_chips_are_no_longer_generated_per_account(self):
        assert "CATALOGUE_STARTERS" not in PAGE

    def test_the_server_no_longer_builds_them(self):
        """It cost roughly six queries per sign-in for a value the page
        stopped reading."""
        source = inspect.getsource(demo_ui)
        assert '"suggestions": await _questions_for_user' not in source
        assert '"suggestions": await _catalogue_questions' not in source


class TestTheChatFrameWidth:
    """A chat widget, not a browser window. Pinned because it is one
    number that quietly governs how the whole surface reads, and nothing
    else in the page would fail if it drifted."""

    def _device_rule(self):
        return PAGE.split(".device {")[1].split("}")[0]

    def test_it_is_a_widget_width(self):
        import re
        width = int(re.search(r"max-width:(\d+)px", self._device_rule()).group(1))
        assert 380 <= width <= 420, (
            "%dpx is outside the range a chat widget occupies" % width
        )

    def test_it_is_centred_rather_than_floated(self):
        body = PAGE.split("body {")[1].split("}")[0]
        assert "justify-content:center" in body
        assert "align-items:center" in body

    def test_the_height_is_untouched(self):
        """Only the width was asked to change."""
        assert "height:min(880px, calc(100dvh - 48px))" in self._device_rule()

    def test_a_real_phone_still_drops_the_frame(self):
        """The 520px breakpoint is a different concern from the desktop
        width - it is where an actual phone goes edge to edge, and
        narrowing the frame must not disturb it."""
        assert "@media (max-width:520px)" in PAGE
        phone = PAGE.split("@media (max-width:520px) {")[1].split("@media")[0]
        assert "max-width:none" in phone
        assert "height:100dvh" in phone

    def test_the_contents_stay_relative(self):
        """Nothing inside is pinned to the old width, which is why the
        column can narrow without re-tuning anything."""
        assert "max-width:85%" in PAGE


class TestBitCardsUseTheProductCardComponent:
    """Consistency was the ask: a Bit row must not look like a different
    app from a product row."""

    def _fn(self, name):
        return PAGE.split("function " + name + "(")[1].split("\n}")[0]

    def test_the_renderer_exists(self):
        assert "function bitCards(" in PAGE

    def test_it_uses_the_same_card_element(self):
        body = self._fn("bitCards")
        for shared in ('"cards"', '"card"', '"row"', '"body"', '"nm"',
                       '"detail"', 'classList.toggle("open")'):
            assert shared in body, "bit rows diverge from product rows: " + shared

    def test_it_draws_no_price(self):
        """A video has no price, and an empty price element would read as
        a missing value rather than an absent concept."""
        body = self._fn("bitCards")
        assert '"pr"' not in body
        assert "rupees(" not in body

    def test_it_says_what_tapping_does(self):
        assert "Opens this Bit in the Zatch app." in PAGE

    def test_the_title_is_written_as_text_not_markup(self):
        """A Bit title is written by whoever uploaded the video."""
        body = self._fn("bitCards")
        assert "textContent" in body
        assert "innerHTML" not in re.sub(r"//.*", "", body)

    def test_the_stream_carries_bits(self):
        assert 'name === "bits"' in PAGE

    def test_a_saved_turn_redraws_them(self):
        assert "if (m.bits) bitCards(m.bits);" in PAGE

    def test_they_are_stored_on_the_message(self):
        push = PAGE.split("thread.messages.push({role: \"bot\"")[1].split("}")[0]
        assert "bits" in push
