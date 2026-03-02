import fs from "fs";
import * as Y from "yjs";

function readStdin() {
  const input = fs.readFileSync(0, "utf-8").trim();
  if (!input) {
    throw new Error("Missing JSON input on stdin");
  }
  return JSON.parse(input);
}

function getMapValue(map, key) {
  if (!map) return undefined;
  if (typeof map.get === "function") {
    return map.get(key);
  }
  return map[key];
}

function setMapValue(map, key, value) {
  if (!map) return;
  if (typeof map.set === "function") {
    map.set(key, value);
  } else {
    map[key] = value;
  }
}

function ensureYMap(value) {
  if (value instanceof Y.Map || (value && value.constructor && value.constructor.name === "YMap")) {
    return { map: value, created: false };
  }
  const map = new Y.Map();
  if (value && typeof value === "object") {
    for (const [key, val] of Object.entries(value)) {
      map.set(key, val);
    }
  }
  return { map, created: true };
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
      } catch (_err) {}
    }
    return Y.encodeStateAsUpdateV2(doc);
  }

  if (vector) {
    try {
      return Y.encodeStateAsUpdate(doc, vector);
    } catch (_err) {}
  }
  return Y.encodeStateAsUpdate(doc);
}

const input = readStdin();
const docState = input.doc_state;
const stateVector = input.state_vector;
const renames = input.renames || [];

if (!Array.isArray(docState) || !Array.isArray(renames)) {
  throw new Error("Invalid input: doc_state and renames must be arrays");
}

const doc = new Y.Doc();
const updateVersion = applyDocState(doc, docState);

const dataRoot = doc.getMap("data");
const database = getMapValue(dataRoot, "database");
if (!database) {
  throw new Error("Missing database map in collab data");
}

const fieldsRaw = getMapValue(database, "fields");
if (!fieldsRaw) {
  throw new Error("Missing fields map in database collab");
}
const { map: fields, created: fieldsCreated } = ensureYMap(fieldsRaw);
if (fieldsCreated) {
  setMapValue(database, "fields", fields);
}

for (const item of renames) {
  const fieldId = item.field_id;
  const newName = item.new_name;
  if (!fieldId || typeof newName !== "string") continue;
  const rawField = getMapValue(fields, fieldId);
  if (!rawField) continue;
  const { map: fieldMap, created: fieldCreated } = ensureYMap(rawField);
  if (fieldCreated) {
    setMapValue(fields, fieldId, fieldMap);
  }
  setMapValue(fieldMap, "name", newName);
}

const update = encodeDocUpdate(doc, stateVector, updateVersion);
process.stdout.write(JSON.stringify({ update: Array.from(update) }));
