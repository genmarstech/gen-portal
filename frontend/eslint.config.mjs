// ESLint flat config.
//
// The dependencies were in package.json and the `lint` script was in there too,
// but there was no config file — so `next lint` dropped into an interactive
// "How would you like to configure ESLint?" prompt. On a terminal that is a
// question; in CI it is a hang, then a failure with no useful message.
//
// Nothing had noticed because nothing ran lint until this repo got a pipeline.
import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

export default [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  { ignores: [".next/**", "node_modules/**"] },
];
