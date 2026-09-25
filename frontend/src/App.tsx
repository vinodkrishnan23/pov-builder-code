import { apiGet } from './api/client';

export default function App() {
  void apiGet;
  return (
    <main>
      <h1>Untitled POC</h1>
      <section data-testid="UC-1"><h2>UC-1</h2><div data-testid="UC-1" /></section>
      <section data-testid="UC-2"><h2>UC-2</h2><div data-testid="UC-2" /></section>
      <section data-testid="UC-3"><h2>UC-3</h2><div data-testid="UC-3" /></section>
      <section data-testid="UC-4"><h2>UC-4</h2><div data-testid="UC-4" /></section>
    </main>
  );
}
