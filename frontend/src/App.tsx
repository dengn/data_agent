import Sidebar from "./components/Sidebar";
import ChatPanel from "./components/ChatPanel";
import "./App.css";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <ChatPanel />
    </div>
  );
}
