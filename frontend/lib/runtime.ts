export const IS_FORMAL_CLOUD = process.env.NEXT_PUBLIC_FORMAL_AUTH_REQUIRED === "true";

export const PRIVATE_STORAGE_LABEL = IS_FORMAL_CLOUD ? "加密云端" : "本机";

export const ARCHIVE_ACTOR_LABEL = IS_FORMAL_CLOUD ? "家庭空间管理员" : "本机家庭管理员";
