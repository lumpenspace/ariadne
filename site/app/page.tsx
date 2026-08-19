const sourceRows = [
  {
    label: "Archive first",
    text: "Read local X/Twitter exports and generic CSV/JSON/JSONL dumps before touching the network.",
  },
  {
    label: "Hydrate cheaply",
    text: "Use public oEmbed for text when a tweet URL is known, with clear limits around missing parent IDs.",
  },
  {
    label: "Spend last",
    text: "Call X API v2 only when the user opts in for missing parents, quotes, or timelines.",
  },
];

const commands = [
  "ariadne interactive",
  "ariadne --archive archive.zip --for-user alice --since 2020-01-01 --format raft",
  "ariadne --for-user alice --unofficial-rss --format json",
];

const outputs = [
  "OpenAI-style message lists",
  "Normalized conversation graphs",
  "Markdown reading views",
  "Raft-ready JSONL documents",
];

export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="heroShade" />
        <div className="heroInner">
          <p className="eyebrow">Archive-first tweet reconstruction</p>
          <h1 id="hero-title">ariadne</h1>
          <p className="lede">
            Rebuild the branch around reply tweets, include quote context, prune
            duplicate subsets, and export conversations as clean AI input.
          </p>
          <div className="heroActions" aria-label="Primary actions">
            <a className="button primary" href="https://github.com/lumpenspace/ariadne">
              GitHub
            </a>
            <a className="button secondary" href="#raft">
              Raft export
            </a>
          </div>
          <pre className="commandBlock" aria-label="Example command">
            <code>{commands[1]}</code>
          </pre>
        </div>
      </section>

      <section className="band introBand">
        <div className="sectionGrid">
          <div>
            <p className="eyebrow dark">Why this exists</p>
            <h2>Tweets are not useful memories until the conversation is back.</h2>
          </div>
          <p>
            A reply without its parent is a fragment. A quote without quoted
            context is a dangling pointer. ariadne turns messy social
            exports into provenance-preserving documents that downstream RAG and
            fine-tuning systems can actually reason over.
          </p>
        </div>
      </section>

      <section className="band">
        <div className="cards three">
          {sourceRows.map((row) => (
            <article className="card" key={row.label}>
              <h3>{row.label}</h3>
              <p>{row.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="band split" id="raft">
        <div>
          <p className="eyebrow dark">For Raft</p>
          <h2>One thread, one retrieval document.</h2>
          <p>
            The `raft` renderer emits JSONL where every reconstructed branch is
            a single candidate memory: plain text for embedding, structured
            messages for attribution, and metadata for dedupe and evaluation.
          </p>
          <ul className="checkList">
            {outputs.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <pre className="codePanel" aria-label="Raft JSONL output example">
          <code>{`{
  "format": "raft.documents.v1",
  "id": "ariadne:1002",
  "kind": "tweet_conversation",
  "text": "@alice: root\\n\\n@bob: reply",
  "metadata": {
    "tweet_ids": ["1001", "1002"],
    "participants": ["@alice", "@bob"]
  }
}`}</code>
        </pre>
      </section>

      <section className="band">
        <div className="commandGrid">
          {commands.map((command) => (
            <pre className="miniCommand" key={command}>
              <code>{command}</code>
            </pre>
          ))}
        </div>
      </section>

      <footer>
        <span>Experimental, explicit about source quality.</span>
        <a href="https://github.com/lumpenspace/raft">Built with Raft in mind</a>
      </footer>
    </main>
  );
}
