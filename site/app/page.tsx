"use client";

import { useEffect, useState } from "react";

type DemoStep = {
  id: string;
  number: string;
  title: string;
  summary: string;
  command: string;
  output: string[];
};

const demoSteps: DemoStep[] = [
  {
    id: "import",
    number: "01",
    title: "Import",
    summary: "Normalize archives once, then keep the originals out of the hot path.",
    command: "ariadne dumps import ~/DATA/tpot_dump --name tpot",
    output: [
      "◆ detecting parquet",
      "◆ indexing 24,402,299 posts",
      "✓ saved as tpot",
    ],
  },
  {
    id: "index",
    number: "02",
    title: "Index",
    summary: "Store text, identities, reply edges, quotes, and full-text search locally.",
    command: "ariadne dumps list",
    output: [
      "personal   14,982 posts",
      "community  382,685 posts",
      "tpot       24,402,299 posts",
    ],
  },
  {
    id: "explore",
    number: "03",
    title: "Explore",
    summary: "Search every imported archive together, or narrow the scope deliberately.",
    command: "ariadne dumps search 'golden thread' --user alice",
    output: [
      "◆ searching all archives",
      "✓ 3 matches · 2 archives",
      "» show 189331742…",
    ],
  },
  {
    id: "reconstruct",
    number: "04",
    title: "Reconstruct",
    summary: "Follow reply parents and quote context across archive boundaries.",
    command: "ariadne build --for-user alice --since 2024-01-01",
    output: [
      "◆ selected 152 starting posts",
      "◆ following cross-archive parents",
      "✓ 141 complete branches",
    ],
  },
  {
    id: "render",
    number: "05",
    title: "Render",
    summary: "Turn complete branches into reading views, graphs, chat, or Raft documents.",
    command: "ariadne build --for-user alice --format raft -o alice.jsonl",
    output: [
      "◆ pruning duplicate subsets",
      "◆ attaching quote context",
      "✓ wrote alice.jsonl",
    ],
  },
];

const toc = [
  ["what", "What it does"],
  ["install", "Install"],
  ["archives", "Archive library"],
  ["explore", "Explore"],
  ["reconstruct", "Reconstruct"],
  ["outputs", "Outputs & API"],
  ["sources", "Source truth"],
];

const archiveKinds = [
  ["Personal export", "Folder or ZIP", "Tweets, authorship, replies, quotes"],
  ["Community Archive", "CSV folder or ZIP", "Many donated accounts and their links"],
  ["Parquet collection", "Directory", "Bulk datasets such as TPOT"],
  ["Generic dump", "CSV, JSON, JSONL", "Mapped tweet-like records"],
];

const sourceRows = [
  ["Imported dumps", "text + structure", "local", "automatic"],
  ["One-shot archive", "text + structure", "local", "explicit"],
  ["Cache", "previously resolved records", "local", "automatic"],
  ["Community Archive", "text + structure", "network", "opt-in"],
  ["oEmbed / Nitter / XCancel", "mostly text", "network", "opt-in"],
  ["X API / twitterapi.io", "text + structure", "network", "opt-in"],
];

function CodeBlock({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="codeBlock">
      <pre>
        <code>{children}</code>
      </pre>
      <button type="button" onClick={copy} aria-label="Copy command">
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}

function ThreadLab({ step }: { step: DemoStep }) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <aside className={`lab${drawerOpen ? " open" : ""}`} aria-label="Ariadne thread lab">
      <button
        className="labTab"
        type="button"
        onClick={() => setDrawerOpen((value) => !value)}
        aria-expanded={drawerOpen}
      >
        <span className="threadSigil">⌇</span>
        thread lab
        <span className="labTabState">{step.number} / 05</span>
      </button>
      <div className="labModule" data-stage={step.id}>
        <div className="labHeader">
          <span>┌─[ live branch ]</span>
          <span>[ {step.number} / 05 ]─┐</span>
        </div>

        <div className="archiveMap" aria-label="A reply branch resolved across three archives">
          <div className="mapGrid" aria-hidden="true" />
          <div className="archiveBadge badgePersonal">personal</div>
          <div className="archiveBadge badgeCommunity">community</div>
          <div className="archiveBadge badgeTpot">tpot</div>

          <article className="tweetNode nodeTarget">
            <span className="nodeDot" />
            <div>
              <b>@alice</b>
              <p>the piece I remembered</p>
            </div>
            <small>target</small>
          </article>
          <article className="tweetNode nodeParent">
            <span className="nodeDot" />
            <div>
              <b>@mira</b>
              <p>replying from another box</p>
            </div>
            <small>parent</small>
          </article>
          <article className="tweetNode nodeRoot">
            <span className="nodeDot" />
            <div>
              <b>@sol</b>
              <p>the beginning of the thread</p>
            </div>
            <small>root</small>
          </article>
          <div className="edge edgeOne" aria-hidden="true" />
          <div className="edge edgeTwo" aria-hidden="true" />
          <div className="edgePulse pulseOne" aria-hidden="true" />
          <div className="edgePulse pulseTwo" aria-hidden="true" />
        </div>

        <div className="terminal" aria-live="polite">
          <div className="terminalBar">
            <i />
            <i />
            <i />
            <span>ariadne · {step.title.toLowerCase()}</span>
          </div>
          <div className="terminalBody">
            <p>
              <span className="prompt">»</span> {step.command}
            </p>
            {step.output.map((line) => (
              <p className="terminalOutput" key={line}>
                {line}
              </p>
            ))}
            <span className="caret" aria-hidden="true" />
          </div>
        </div>
      </div>
    </aside>
  );
}

export default function Home() {
  const [demoIndex, setDemoIndex] = useState(3);
  const [activeSection, setActiveSection] = useState("what");
  const demo = demoSteps[demoIndex];

  useEffect(() => {
    const sections = toc
      .map(([id]) => document.getElementById(id))
      .filter((section): section is HTMLElement => section !== null);
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible?.target.id) setActiveSection(visible.target.id);
      },
      { rootMargin: "-18% 0px -68%", threshold: [0, 0.2, 0.6] },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  return (
    <main>
      <header className="topbar">
        <a className="identity" href="#top" aria-label="Ariadne home">
          <span className="threadSigil">⌇</span>
          <span>ariadne</span>
        </a>
        <span className="family">⟡ hyperplex</span>
        <span className="tagline">find the conversation</span>
        <nav aria-label="Primary navigation">
          <a href="#what">Docs</a>
          <a href="https://github.com/lumpenspace/ariadne">GitHub</a>
        </nav>
      </header>

      <section className="hero" id="top" aria-labelledby="hero-title">
        <div className="heroGrid" aria-hidden="true" />
        <div className="heroArtifact" aria-hidden="true" />
        <div className="heroContent">
          <p className="heroKicker">archive-first conversation reconstruction</p>
          <h1 id="hero-title">Follow the thread.</h1>
          <p className="heroLede">
            Ariadne turns fragmented X/Twitter archives into complete reply branches,
            searchable local memory, and clean input for people, models, and Raft.
          </p>
          <div className="heroActions">
            <a className="primaryAction" href="#install">
              start with an archive <span>↓</span>
            </a>
            <a className="secondaryAction" href="#reconstruct">
              see reconstruction
            </a>
          </div>
          <div className="heroCommand" aria-label="Quick start commands">
            <span className="windowDots">● ● ●</span>
            <code>
              <span>$</span> uv tool install &apos;ariadne-x[parquet]&apos;
              <br />
              <span>$</span> ariadne dumps interactive
            </code>
          </div>
          <dl className="heroFacts">
            <div>
              <dt>04</dt>
              <dd>archive kinds</dd>
            </div>
            <div>
              <dt>05</dt>
              <dd>renderers</dd>
            </div>
            <div>
              <dt>00</dt>
              <dd>network calls by default</dd>
            </div>
          </dl>
        </div>
      </section>

      <div className="docsLayout">
        <nav className="toc" aria-label="Documentation contents">
          <p>┌─[ documentation ]</p>
          <ol>
            {toc.map(([id, label]) => (
              <li key={id}>
                <a className={activeSection === id ? "active" : ""} href={`#${id}`}>
                  {label}
                </a>
              </li>
            ))}
          </ol>
          <span className="tocVersion">└─ v0.5 · alpha</span>
        </nav>

        <article className="docs">
          <section id="what" className="docSection firstSection">
            <p className="kicker">00 · the map</p>
            <h2>One remembered post. Three archives. One conversation again.</h2>
            <p className="intro">
              Social exports preserve posts, but the meaning often lives elsewhere: in a
              parent from another account, a quoted post, or a branch split across datasets.
              Ariadne normalizes those sources and follows their IDs back to the root.
            </p>
            <ol className="mission">
              {demoSteps.map((step, index) => (
                <li key={step.id}>
                  <button
                    type="button"
                    className={index === demoIndex ? "active" : ""}
                    onClick={() => setDemoIndex(index)}
                    aria-pressed={index === demoIndex}
                  >
                    <span className="missionNumber">{step.number}</span>
                    <span>
                      <b>{step.title}</b>
                      <small>{step.summary}</small>
                      <code>{step.command}</code>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </section>

          <section id="install" className="docSection">
            <p className="kicker">01 · install</p>
            <h2>The package is ariadne-x. The tool is ariadne.</h2>
            <p>
              Python 3.11 or newer is required. The <code>parquet</code> extra adds DuckDB
              for bulk Parquet imports; personal and Community archives work in the base
              install.
            </p>
            <CodeBlock>{`uv tool install 'ariadne-x[parquet]'
ariadne --help`}</CodeBlock>
            <h3>Run from a checkout</h3>
            <CodeBlock>{`git clone https://github.com/lumpenspace/ariadne
cd ariadne
uv sync --extra dev --extra parquet
uv run ariadne --help`}</CodeBlock>
            <div className="callout">
              <span>⌇</span>
              <p>
                <strong>Two interactive modes:</strong> <code>ariadne interactive</code>
                builds conversations. <code>ariadne dumps interactive</code> explores the
                persistent archive library.
              </p>
            </div>
          </section>

          <section id="archives" className="docSection">
            <p className="kicker">02 · archive library</p>
            <h2>Import once. Search locally. Keep the source out of the way.</h2>
            <p>
              Each source becomes a normalized, indexed SQLite database under
              <code>~/.ariadne/dumps</code>. Set <code>ARIADNE_HOME</code> to move the
              settings directory. The source is never modified.
            </p>
            <CodeBlock>{`ariadne dumps import ~/DATA/tpot_dump --name tpot
ariadne dumps import ~/Downloads/community.zip --name community
ariadne dumps import ~/Downloads/twitter-archive.zip --name personal
ariadne dumps list`}</CodeBlock>
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Shape</th>
                    <th>What survives</th>
                  </tr>
                </thead>
                <tbody>
                  {archiveKinds.map((row) => (
                    <tr key={row[0]}>
                      <td>{row[0]}</td>
                      <td>{row[1]}</td>
                      <td>{row[2]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="finePrint">
              Kind detection is automatic. Use <code>--kind community-csv</code>,
              <code>parquet</code>, <code>twitter-archive</code>, or
              <code>tweets-file</code> when a source is ambiguous. Re-importing a name
              replaces that normalized database only after a successful import.
            </p>
          </section>

          <section id="explore" className="docSection">
            <p className="kicker">03 · explore</p>
            <h2>Search is cross-archive unless you tell it not to be.</h2>
            <p>
              The interactive explorer changes scope, browses users, searches text, opens
              timelines, and shows the thread context available across every selected dump.
            </p>
            <CodeBlock>{`ariadne dumps interactive
ariadne dumps users --top 25
ariadne dumps search 'local-first' --user alice
ariadne dumps user alice --since 2024-01-01 --limit 50
ariadne dumps show https://x.com/alice/status/1234567890`}</CodeBlock>
            <div className="callout blueCallout">
              <span>⟡</span>
              <p>
                Add repeatable <code>--dump NAME</code> to restrict a query. Without it,
                duplicate post IDs are merged and complementary metadata from different
                archives is preserved.
              </p>
            </div>
          </section>

          <section id="reconstruct" className="docSection">
            <p className="kicker">04 · reconstruct</p>
            <h2>Build the user&apos;s conversations, not a bag of isolated posts.</h2>
            <p>
              Imported dumps participate automatically. Ariadne selects the user&apos;s
              starting posts, recursively follows reply parents, attaches quote context,
              and prunes branches already contained in longer branches. Every step may
              cross archive boundaries.
            </p>
            <CodeBlock>{`ariadne build \
  --for-user alice \
  --since 2024-01-01 \
  --format markdown \
  --output alice-conversations.md`}</CodeBlock>
            <div className="threadRule">
              <div>
                <span className="ruleNode" />
                <b>start</b>
                <small>alice · personal</small>
              </div>
              <i />
              <div>
                <span className="ruleNode" />
                <b>parent</b>
                <small>mira · community</small>
              </div>
              <i />
              <div>
                <span className="ruleNode" />
                <b>root</b>
                <small>sol · tpot</small>
              </div>
            </div>
            <ul className="detailList">
              <li>
                <strong><code>--since</code></strong> limits starting posts, not the older
                ancestors needed to complete them.
              </li>
              <li>
                <strong><code>--dump NAME</code></strong> narrows the library;
                <code>--no-dumps</code> disables it.
              </li>
              <li>
                <strong>Large timelines</strong> above 10,000 posts require a narrower date
                or an explicit <code>--dump-limit</code>.
              </li>
              <li>
                <strong>Network reads</strong> stay off unless you enable oEmbed, RSS,
                Community Archive, X API, or twitterapi.io.
              </li>
            </ul>
          </section>

          <section id="outputs" className="docSection">
            <p className="kicker">05 · outputs & API</p>
            <h2>Keep the graph. Read the thread. Feed the model.</h2>
            <div className="outputGrid">
              <article>
                <span>json</span>
                <p>Normalized tweets, branch paths, quotes, warnings, and provenance.</p>
              </article>
              <article>
                <span>markdown</span>
                <p>A compact reading view for humans and notebooks.</p>
              </article>
              <article>
                <span>messages</span>
                <p>OpenAI-like turns with tweet metadata and participant names.</p>
              </article>
              <article>
                <span>openai</span>
                <p>Strict role, name, and content objects for API input.</p>
              </article>
              <article>
                <span>raft</span>
                <p>One retrieval document per reconstructed branch, emitted as JSONL.</p>
              </article>
            </div>
            <h3>Python, without a file round-trip</h3>
            <CodeBlock>{`import ariadne

result = ariadne.build(
    for_user="alice",
    dump=["personal", "community", "tpot"],
    since="2024-01-01",
)

for document in result.raft_documents():
    print(document["metadata"]["target_id"])`}</CodeBlock>
            <p className="finePrint">
              The lower-level API also exposes <code>LocalDumpsClient</code>,
              <code>TweetStore</code>, renderers, cache helpers, and source loaders. See the
              repository&apos;s <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/API.md">Python API reference</a>.
            </p>
          </section>

          <section id="sources" className="docSection">
            <p className="kicker">06 · source truth</p>
            <h2>Text is not structure, and cheap is not complete.</h2>
            <p>
              A reply branch can only continue when a source knows the parent post ID.
              oEmbed and RSS can improve text, but rich archives or structured APIs are what
              reveal the edges.
            </p>
            <div className="tableWrap sourceTable">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Knows</th>
                    <th>Where</th>
                    <th>Policy</th>
                  </tr>
                </thead>
                <tbody>
                  {sourceRows.map((row) => (
                    <tr key={row[0]}>
                      {row.map((cell) => (
                        <td key={cell}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>Nitter and XCancel RSS</h3>
            <p className="finePrint">
              <code>--target-user</code> tries <code>https://nitter.net</code> and{" "}
              <code>https://rss.xcancel.com</code> unless you add{" "}
              <code>--no-unofficial-rss</code>. For other runs, enable the fallback with{" "}
              <code>--unofficial-rss</code>; customize it with repeatable{" "}
              <code>--rss-base</code> or <code>--rss-url-template</code>. These services are
              unsupported and fragile, usually expose only recent feed items, and usually
              omit reply-parent metadata. They can recover text, but they cannot reliably
              complete a branch by themselves.
            </p>
            <div className="callout">
              <span>✓</span>
              <p>
                <strong>Local first is the default.</strong> Owner-only permissions are used
                for the settings directory on POSIX systems, imported databases remain on
                your machine, and removing a dump never touches its source archive.
              </p>
            </div>
            <h3>Development</h3>
            <CodeBlock>{`uv sync --extra dev --extra parquet
uv run pytest
uv run ruff check .`}</CodeBlock>
          </section>
        </article>

        <ThreadLab step={demo} />
      </div>

      <footer>
        <div>
          <span className="threadSigil">⌇</span>
          <strong>ariadne</strong>
          <span> · </span>
          <span className="family">⟡ hyperplex</span>
        </div>
        <nav aria-label="Footer navigation">
          <a href="https://github.com/lumpenspace/ariadne">GitHub</a>
          <a href="https://github.com/lumpenspace/ariadne/blob/main/README.md">README</a>
          <a href="https://github.com/lumpenspace/ariadne/blob/main/LICENSE">License</a>
        </nav>
        <p>The thread was there all along.</p>
      </footer>
    </main>
  );
}
