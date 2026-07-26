const ALLOWED_STATUS = new Set(["available", "planned", "blocked"]);

export function createVisualisationRegistry(contract) {
  if (!contract || !Array.isArray(contract.visualisations) || !Array.isArray(contract.routes)) {
    throw new Error("The visualisation contract is incomplete.");
  }
  const routes = new Map(contract.routes.map((route) => [route.route_id, Object.freeze({...route})]));
  const entries = new Map();
  for (const definition of contract.visualisations) {
    if (!definition.visualisation_id || entries.has(definition.visualisation_id)) {
      throw new Error("The visualisation contract contains a missing or duplicate identifier.");
    }
    if (!routes.has(definition.route_id) || !ALLOWED_STATUS.has(definition.status)) {
      throw new Error(`Visualisation ${definition.visualisation_id} has an invalid route or status.`);
    }
    entries.set(definition.visualisation_id, Object.freeze({...definition}));
  }
  return Object.freeze({
    contractVersion: contract.contract_version,
    designSystemVersion: contract.design_system_version,
    defaultRoute: contract.default_route,
    routes,
    get: (id) => entries.get(id),
    list: (options = {}) => [...entries.values()].filter((entry) => !options.route || entry.route_id === options.route)
  });
}
