import { create } from 'zustand';
type Selection = { selectedTrainRunId?:string; selectedBlockId?:string; selectedConflictId?:string; selectTrain:(id?:string)=>void; selectConflict:(id?:string, blockId?:string)=>void };
export const useSelectionStore = create<Selection>((set)=>({selectTrain:(selectedTrainRunId)=>set({selectedTrainRunId}),selectConflict:(selectedConflictId,selectedBlockId)=>set({selectedConflictId,selectedBlockId})}));
