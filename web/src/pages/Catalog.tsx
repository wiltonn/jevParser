import { useState } from "react";
import { Link } from "react-router-dom";
import { ErrorNote, Loading, PageHead } from "../components/ui";
import { api } from "../lib/api";
import { useAction, useApi, useMe } from "../lib/hooks";
import type { CatalogOem } from "../lib/types";

export default function Catalog() {
  const tree = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const me = useMe();
  const [oemName, setOemName] = useState("");
  const [promptName, setPromptName] = useState("");
  const addOem = useAction(() => api.post("/oems", { name: oemName, prompt_name: promptName || null }), [["catalog"]]);

  return (
    <div className="page">
      <PageHead title="Catalog" sub="OEMs, their brands and models, and the model years with documents on file. Confirming a document in the library adds any missing entries." />
      {tree.isLoading && <Loading />}
      {tree.data?.map((oem) => <OemBlock key={oem.id} oem={oem} />)}
      {me.data?.role === "admin" && (
        <div className="panel">
          <h3>Add an OEM</h3>
          <div className="form-row">
            <div className="field"><label>Name</label><input type="text" value={oemName} onChange={(e) => setOemName(e.target.value)} placeholder="Ford Motor Company" /></div>
            <div className="field"><label>Name in questions</label><input type="text" value={promptName} onChange={(e) => setPromptName(e.target.value)} placeholder="Ford" /></div>
            <button className="btn primary" disabled={!oemName || addOem.isPending}
              onClick={() => addOem.mutate(undefined, { onSuccess: () => { setOemName(""); setPromptName(""); } })}>Add OEM</button>
          </div>
          <p className="sub" style={{ margin: "8px 0 0" }}>An OEM needs a ruleset before its documents can be parsed; clone one under Admin → Rulesets.</p>
          <ErrorNote error={addOem.error} />
        </div>
      )}
    </div>
  );
}

function OemBlock({ oem }: { oem: CatalogOem }) {
  const [brand, setBrand] = useState("");
  const addBrand = useAction(() => api.post(`/oems/${oem.id}/brands`, { name: brand }), [["catalog"]]);
  return (
    <>
      <h2>{oem.name} <span className="chip">questions say “{oem.prompt_name}”</span></h2>
      <div className="cols">
        {oem.brands.map((b) => <BrandCard key={b.id} brand={b} />)}
        <div className="panel">
          <h3>Add a brand</h3>
          <div className="form-row">
            <input type="text" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Brand name" />
            <button className="btn" disabled={!brand} onClick={() => addBrand.mutate(undefined, { onSuccess: () => setBrand("") })}>Add</button>
          </div>
          <ErrorNote error={addBrand.error} />
        </div>
      </div>
    </>
  );
}

function BrandCard({ brand }: { brand: CatalogOem["brands"][number] }) {
  const [model, setModel] = useState("");
  const addModel = useAction(() => api.post(`/brands/${brand.id}/models`, { name: model }), [["catalog"]]);
  return (
    <div className="panel">
      <h3>{brand.name}</h3>
      {brand.models.length === 0 && <p className="sub">No models yet.</p>}
      <table className="plain"><tbody>
        {brand.models.map((m) => (
          <tr key={m.id}>
            <td><Link to={`/catalog/models/${m.id}`}><b>{m.name}</b></Link></td>
            <td>{m.years.map((y) => (
              <span key={y.id} className={`chip${y.documents ? " accent" : ""}`} style={{ marginRight: 4 }}
                title={`${y.documents} document(s)`}>{y.year}</span>
            ))}</td>
          </tr>
        ))}
      </tbody></table>
      <div className="form-row" style={{ marginTop: 10 }}>
        <input type="text" value={model} onChange={(e) => setModel(e.target.value)} placeholder="New model" />
        <button className="btn sm" disabled={!model} onClick={() => addModel.mutate(undefined, { onSuccess: () => setModel("") })}>Add model</button>
      </div>
    </div>
  );
}
