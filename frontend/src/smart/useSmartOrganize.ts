import { useContext } from "react";
import { SmartOrganizeContext } from "./SmartOrganizeContext";

export function useSmartOrganize() {
  const context = useContext(SmartOrganizeContext);
  if (!context) throw new Error("useSmartOrganize must be used within SmartOrganizeProvider");
  return context;
}
