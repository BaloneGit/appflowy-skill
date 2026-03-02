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

function deleteMapValue(map, key) {
  if (!map) return;
  if (typeof map.delete === "function") {
    map.delete(key);
  } else {
    delete map[key];
  }
}

function mapEntries(map) {
  if (!map) return [];
  if (typeof map.entries === "function") {
    return Array.from(map.entries());
  }
  if (typeof map === "object") {
    return Object.entries(map);
  }
  return [];
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

function removeArrayItems(arr, predicate) {
  if (!arr) return;
  if (Array.isArray(arr)) {
    for (let idx = arr.length - 1; idx >= 0; idx -= 1) {
      if (predicate(arr[idx])) {
        arr.splice(idx, 1);
      }
    }
    return;
  }
  if (typeof arr.toArray === "function" && typeof arr.delete === "function") {
    const list = arr.toArray();
    for (let idx = list.length - 1; idx >= 0; idx -= 1) {
      if (predicate(list[idx])) {
        arr.delete(idx, 1);
      }
    }
  }
}

function itemFieldId(item) {
  if (!item) return null;
  if (typeof item.get === "function") {
    return item.get("field_id");
  }
  if (typeof item === "object") {
    return item.field_id || null;
  }
  return null;
}

function itemId(item) {
  if (!item) return null;
  if (typeof item.get === "function") {
    return item.get("id");
  }
  if (typeof item === "object") {
    return item.id || null;
  }
  return null;
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
const fieldIds = input.field_ids || [];

if (!Array.isArray(docState) || !Array.isArray(fieldIds)) {
  throw new Error("Invalid input: doc_state and field_ids must be arrays");
}

const targetIds = new Set(fieldIds.filter((id) => typeof id === "string" && id.length > 0));
if (targetIds.size === 0) {
  throw new Error("field_ids is empty");
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

for (const fieldId of targetIds) {
  deleteMapValue(fields, fieldId);
}

const viewsRaw = getMapValue(database, "views");
if (viewsRaw) {
  const { map: views, created: viewsCreated } = ensureYMap(viewsRaw);
  if (viewsCreated) {
    setMapValue(database, "views", views);
  }
  for (const [_viewId, rawView] of mapEntries(views)) {
    const { map: view, created: viewCreated } = ensureYMap(rawView);
    if (viewCreated) {
      setMapValue(views, _viewId, view);
    }

    const fieldOrders = getMapValue(view, "field_orders");
    removeArrayItems(fieldOrders, (item) => targetIds.has(itemId(item)));

    const fieldSettingsRaw = getMapValue(view, "field_settings");
    if (fieldSettingsRaw) {
      const { map: fieldSettings, created: settingsCreated } = ensureYMap(fieldSettingsRaw);
      if (settingsCreated) {
        setMapValue(view, "field_settings", fieldSettings);
      }
      for (const fieldId of targetIds) {
        deleteMapValue(fieldSettings, fieldId);
      }
    }

    const filters = getMapValue(view, "filters");
    removeArrayItems(filters, (item) => targetIds.has(itemFieldId(item)));

    const sorts = getMapValue(view, "sorts");
    removeArrayItems(sorts, (item) => targetIds.has(itemFieldId(item)));

    const groups = getMapValue(view, "groups");
    removeArrayItems(groups, (item) => targetIds.has(itemFieldId(item)));
  }
}

const update = encodeDocUpdate(doc, stateVector, updateVersion);
process.stdout.write(JSON.stringify({ update: Array.from(update) }));
