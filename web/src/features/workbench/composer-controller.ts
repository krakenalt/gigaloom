import { useRef, useState } from "react";

import type { ChatMention } from "../../chat-mentions";
import type { SkillMention } from "../../skill-mentions";

export function useComposerController() {
  const [prompt, setPrompt] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<SkillMention[]>([]);
  const [selectedChats, setSelectedChats] = useState<ChatMention[]>([]);
  const [builtinTools, setBuiltinTools] = useState<string[]>([]);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [toolPickerOpen, setToolPickerOpen] = useState(false);
  const [toolSearch, setToolSearch] = useState("");
  const [draggingFiles, setDraggingFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [composerCaret, setComposerCaret] = useState(0);
  const [atSelection, setAtSelection] = useState(0);

  return {
    atSelection,
    builtinTools,
    composerCaret,
    composerRef,
    draggingFiles,
    fileInputRef,
    modelMenuOpen,
    plusMenuOpen,
    prompt,
    selectedChats,
    selectedSkills,
    setAtSelection,
    setBuiltinTools,
    setComposerCaret,
    setDraggingFiles,
    setModelMenuOpen,
    setPlusMenuOpen,
    setPrompt,
    setSelectedChats,
    setSelectedSkills,
    setToolPickerOpen,
    setToolSearch,
    toolPickerOpen,
    toolSearch,
  };
}
