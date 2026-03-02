import fs from "fs";
import * as Y from "yjs";

function readStdin() {
  const input = fs.readFileSync(0, "utf-8").trim();
  if (!input) {
    throw new Error("Missing JSON input on stdin");
  }
  return JSON.parse(input);
}

function asArray(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.slice();
  if (typeof value.toArray === "function") return value.toArray();
  if (typeof value.values === "function") return Array.from(value.values());
  if (typeof value[Symbol.iterator] === "function") return Array.from(value);
  return [];
}

function getMapValue(map, key) {
  if (!map) return undefined;
  if (typeof map.get === "function") {
    return map.get(key);
  }
  return map[key];
}


function deleteFromArray(arr, idx) {
  if (!arr) return;
  if (Array.isArray(arr)) {
    if (idx >= 0) arr.splice(idx, 1);
    return;
  }
  if (typeof arr.delete === "function") {
    arr.delete(idx, 1);
  }
}

function listKeys(map) {
  if (!map) return [];
  if (typeof map.keys === "function") {
    return Array.from(map.keys());
  }
  return Object.keys(map);
}

function getRowId(row) {
  if (!row) return undefined;
  if (typeof row === "string") return row;
  return (
    getMapValue(row, "id") ??
    getMapValue(row, "row_id") ??
    getMapValue(row, "rowId")
  );
}

function applyDocState(doc, docState) {
  const update = Uint8Array.from(docState);
  try {
    Y.applyUpdate(doc, update);
    return "v1";
  } catch (v1Err) {
    if (typeof Y.applyUpdateV2 !== "function") {
      throw v1Err;
    }
    Y.applyUpdateV2(doc, update);
    return "v2";
  }
}

function encodeDocUpdate(doc, stateVector, versionHint) {
  const hasStateVector = Array.isArray(stateVector);
  const vector = hasStateVector ? Uint8Array.from(stateVector) : undefined;

  if (versionHint === "v2" && typeof Y.encodeStateAsUpdateV2 === "function") {
    if (vector) {
      try {
        return Y.encodeStateAsUpdateV2(doc, vector);
      } catch (err) {
        // fall through to full-update encoding
      }
    }
    return Y.encodeStateAsUpdateV2(doc);
  }

  if (vector) {
    try {
      return Y.encodeStateAsUpdate(doc, vector);
    } catch (err) {
      // fall through to full-update encoding
    }
  }
  return Y.encodeStateAsUpdate(doc);
}

const input = readStdin();
const docState = input.doc_state;
const stateVector = input.state_vector;
const rowIds = new Set(input.row_ids || []);
const viewIds = input.view_ids || [];

if (!Array.isArray(docState) || !Array.isArray(input.row_ids)) {
  throw new Error("Invalid input: doc_state and row_ids must be arrays");
}

const doc = new Y.Doc();
const updateVersion = applyDocState(doc, docState);

const dataRoot = doc.getMap("data");
const database = getMapValue(dataRoot, "database");
if (!database) {
  throw new Error("Missing database map in collab data");
}

const views = getMapValue(database, "views");
if (!views) {
  throw new Error("Missing views map in database collab");
}

const targetViewIds = viewIds.length > 0 ? viewIds : listKeys(views);

for (const viewId of targetViewIds) {
  const view = getMapValue(views, viewId);
  if (!view) continue;
  const rowOrders = getMapValue(view, "row_orders");
  if (!rowOrders) continue;
  const rows = asArray(rowOrders);
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const row = rows[i];
    const rowId = getRowId(row);
    if (rowIds.has(rowId)) {
      deleteFromArray(rowOrders, i);
    }
  }
  // row_orders 已在原地更新，无需重新 set
}

const update = encodeDocUpdate(doc, stateVector, updateVersion);
const output = { update: Array.from(update) };
process.stdout.write(JSON.stringify(output));
