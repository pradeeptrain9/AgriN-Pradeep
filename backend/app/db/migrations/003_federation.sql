-- Federation: this node's signing identity and its peers.
CREATE TABLE IF NOT EXISTS node_identity (
    node_id         text PRIMARY KEY,
    public_key      text NOT NULL,
    private_key     text NOT NULL,   -- never leaves this table or this node
    country         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Peers whose signed payloads this node will accept. A payload from a node not
-- listed here is rejected: without this, anyone who can reach the endpoint can
-- claim to be a peer.
CREATE TABLE IF NOT EXISTS peer_nodes (
    node_id         text PRIMARY KEY,
    public_key      text NOT NULL,
    base_url        text,
    country         text,
    trusted         boolean NOT NULL DEFAULT false,
    added_at        timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz
);

-- Districts let aggregates be grouped without exposing field geometry.
ALTER TABLE fields ADD COLUMN IF NOT EXISTS district text;
CREATE INDEX IF NOT EXISTS fields_district_idx ON fields (district);
